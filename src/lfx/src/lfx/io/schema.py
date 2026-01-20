"""
模块名称：lfx.io.schema

本模块提供输入 schema 的转换与构建能力，主要用于在 `Pydantic` 与 Langflow 输入定义之间桥接。主要功能包括：
- 功能1：将 JSON Schema 扁平化为单层结构（`flatten_schema`）
- 功能2：将 `Pydantic` 模型字段转换为 Langflow 输入组件（`schema_to_langflow_inputs`）
- 功能3：根据输入组件定义生成 `Pydantic` schema（`create_input_schema`/`create_input_schema_from_dict`）

关键组件：
- `flatten_schema`：处理 `$defs`/`$ref` 与嵌套对象
- `schema_to_langflow_inputs`：字段到输入组件的映射
- `create_input_schema`：输入组件到 `Pydantic` 模型的反向构建

设计背景：保持组件输入定义与校验模型一致，避免手写 schema 与输入表单脱节。
注意事项：包含 `Literal` 与 `eval` 相关逻辑，仅适用于受控输入来源。
"""

from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, create_model

from lfx.inputs.input_mixin import FieldTypes
from lfx.inputs.inputs import (
    BoolInput,
    DictInput,
    DropdownInput,
    FloatInput,
    InputTypes,
    IntInput,
    MessageTextInput,
)
from lfx.schema.dotdict import dotdict

_convert_field_type_to_type: dict[FieldTypes, type] = {
    FieldTypes.TEXT: str,
    FieldTypes.INTEGER: int,
    FieldTypes.FLOAT: float,
    FieldTypes.BOOLEAN: bool,
    FieldTypes.DICT: dict,
    FieldTypes.NESTED_DICT: dict,
    FieldTypes.TABLE: dict,
    FieldTypes.FILE: str,
    FieldTypes.PROMPT: str,
    FieldTypes.CODE: str,
    FieldTypes.OTHER: str,
    FieldTypes.TAB: str,
    FieldTypes.QUERY: str,
}


_convert_type_to_field_type = {
    str: MessageTextInput,
    int: IntInput,
    float: FloatInput,
    bool: BoolInput,
    dict: DictInput,
    list: MessageTextInput,
}


def flatten_schema(root_schema: dict[str, Any]) -> dict[str, Any]:
    """将 JSON RPC 风格 schema 展平成单层 JSON Schema。

    契约：输入 `root_schema`，输出扁平化 schema；若已扁平则原样返回。
    关键路径（三步）：1) 快速判定是否已扁平 2) 递归解析 `$ref`/对象/数组 3) 汇总 `properties`/`required`。
    异常流：无显式异常，依赖调用方提供合法 schema。
    性能瓶颈：深层嵌套时递归成本线性增长。
    排障入口：检查 `$defs` 与 `properties` 是否被正确解析。
    """
    defs = root_schema.get("$defs", {})

    # 性能：已扁平 schema 直接返回，避免递归开销
    props = root_schema.get("properties", {})
    if not defs and all("$ref" not in v and v.get("type") not in ("object", "array") for v in props.values()):
        return root_schema

    flat_props: dict[str, dict[str, Any]] = {}
    required_list: list[str] = []

    def _resolve_if_ref(schema: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in schema:
            ref_name = schema["$ref"].split("/")[-1]
            schema = defs.get(ref_name, {})
        return schema

    def _walk(name: str, schema: dict[str, Any], *, inherited_req: bool) -> None:
        schema = _resolve_if_ref(schema)
        t = schema.get("type")

        # 实现：对象字段展开为点路径
        if t == "object":
            req_here = set(schema.get("required", []))
            for k, subschema in schema.get("properties", {}).items():
                child_name = f"{name}.{k}" if name else k
                _walk(name=child_name, schema=subschema, inherited_req=inherited_req and k in req_here)
            return

        # 实现：数组统一按 `[0]` 展开第一项
        if t == "array":
            items = schema.get("items", {})
            _walk(name=f"{name}[0]", schema=items, inherited_req=inherited_req)
            return

        leaf: dict[str, Any] = {
            k: v
            for k, v in schema.items()
            if k
            in (
                "type",
                "description",
                "pattern",
                "format",
                "enum",
                "default",
                "minLength",
                "maxLength",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "additionalProperties",
                "examples",
            )
        }
        flat_props[name] = leaf
        if inherited_req:
            required_list.append(name)

    # 实现：从根节点开始递归遍历
    root_required = set(root_schema.get("required", []))
    for k, subschema in props.items():
        _walk(k, subschema, inherited_req=k in root_required)

    # 实现：保留描述性元数据
    result: dict[str, Any] = {
        "type": "object",
        "properties": flat_props,
        **{k: v for k, v in root_schema.items() if k not in ("properties", "$defs")},
    }
    if required_list:
        result["required"] = required_list
    return result


def schema_to_langflow_inputs(schema: type[BaseModel]) -> list[InputTypes]:
    """将 `Pydantic` 模型字段转换为 Langflow 输入组件列表。

    契约：输入 `Pydantic` 模型类，输出输入组件列表；字段保持原有必填/描述/标题信息。
    关键路径（三步）：1) 解析注解并处理 `Union`/`list` 2) 识别 `Literal` 枚举 3) 选择输入组件类型。
    异常流：遇到不支持的字段类型时抛 `TypeError`。
    性能瓶颈：字段数量线性增长，复杂类型会增加分支处理成本。
    排障入口：异常信息 `Unsupported field type`。
    """
    inputs: list[InputTypes] = []

    for field_name, model_field in schema.model_fields.items():
        ann = model_field.annotation
        if isinstance(ann, UnionType):
            # 实现：剔除 `None` 以处理可选字段
            non_none_types = [t for t in get_args(ann) if t is not type(None)]
            if len(non_none_types) == 1:
                ann = non_none_types[0]

        is_list = False

        # 注意：未参数化的 `list` 统一按字符串列表处理
        if ann is list:
            is_list = True
            ann = str

        if get_origin(ann) is list:
            is_list = True
            ann = get_args(ann)[0]

        options: list[Any] | None = None
        if get_origin(ann) is Literal:
            options = list(get_args(ann))
            if options:
                ann = type(options[0])

        if get_origin(ann) is Union:
            non_none = [t for t in get_args(ann) if t is not type(None)]
            if len(non_none) == 1:
                ann = non_none[0]

        # 实现：枚举值映射为下拉框
        if options is not None:
            inputs.append(
                DropdownInput(
                    display_name=model_field.title or field_name.replace("_", " ").title(),
                    name=field_name,
                    info=model_field.description or "",
                    required=model_field.is_required(),
                    is_list=is_list,
                    options=options,
                )
            )
            continue

        # 实现：`Any` 回退为文本输入
        if ann is Any:
            inputs.append(
                MessageTextInput(
                    display_name=model_field.title or field_name.replace("_", " ").title(),
                    name=field_name,
                    info=model_field.description or "",
                    required=model_field.is_required(),
                    is_list=is_list,
                )
            )
            continue

        # 实现：基础类型按映射表创建输入组件
        try:
            lf_cls = _convert_type_to_field_type[ann]
        except KeyError as err:
            msg = f"Unsupported field type: {ann}"
            raise TypeError(msg) from err
        inputs.append(
            lf_cls(
                display_name=model_field.title or field_name.replace("_", " ").title(),
                name=field_name,
                info=model_field.description or "",
                required=model_field.is_required(),
                is_list=is_list,
            )
        )

    return inputs


def create_input_schema(inputs: list["InputTypes"]) -> type[BaseModel]:
    """根据输入组件列表生成 `Pydantic` 模型。

    契约：输入 `InputTypes` 列表，输出动态 `Pydantic` 模型；保留 title/description/default。
    关键路径（三步）：1) 解析输入类型与 `options` 2) 处理列表与默认值 3) 组装并构建模型。
    异常流：`inputs` 非列表或 `field_type` 非法时抛 `TypeError`；缺少名称抛 `ValueError`。
    安全：`Literal` 通过 `eval` 构造，仅适用于受控 `options`。
    排障入口：异常信息 `Invalid field type`/`Input name or display_name is required`。
    """
    if not isinstance(inputs, list):
        msg = "inputs must be a list of Inputs"
        raise TypeError(msg)
    fields = {}
    for input_model in inputs:
        field_type = input_model.field_type
        if isinstance(field_type, FieldTypes):
            field_type = _convert_field_type_to_type[field_type]
        else:
            msg = f"Invalid field type: {field_type}"
            raise TypeError(msg)
        if hasattr(input_model, "options") and isinstance(input_model.options, list) and input_model.options:
            literal_string = f"Literal{input_model.options}"
            # 安全：`options` 来自受控输入，避免注入风险
            field_type = eval(literal_string, {"Literal": Literal})  # noqa: S307
        if hasattr(input_model, "is_list") and input_model.is_list:
            field_type = list[field_type]  # type: ignore[valid-type]
        if input_model.name:
            name = input_model.name.replace("_", " ").title()
        elif input_model.display_name:
            name = input_model.display_name
        else:
            msg = "Input name or display_name is required"
            raise ValueError(msg)
        field_dict = {
            "title": name,
            "description": input_model.info or "",
        }
        if input_model.required is False:
            field_dict["default"] = input_model.value  # type: ignore[assignment]
        pydantic_field = Field(**field_dict)

        fields[input_model.name] = (field_type, pydantic_field)

    # 实现：构建并返回输入 schema
    model = create_model("InputSchema", **fields)
    model.model_rebuild()
    return model


def create_input_schema_from_dict(inputs: list[dotdict], param_key: str | None = None) -> type[BaseModel]:
    """从字典化输入定义生成 `Pydantic` 模型。

    契约：输入 `dotdict` 列表，输出动态模型；可选 `param_key` 用于包裹成内层模型。
    关键路径（三步）：1) 解析字段类型与枚举 2) 处理列表与默认值 3) 可选包裹为内层模型。
    异常流：`inputs` 非列表抛 `TypeError`；缺少名称抛 `ValueError`。
    安全：`Literal` 通过 `eval` 构造，仅适用于受控 `options`。
    排障入口：异常信息 `Input name or display_name is required`。
    """
    if not isinstance(inputs, list):
        msg = "inputs must be a list of Inputs"
        raise TypeError(msg)
    fields = {}
    for input_model in inputs:
        field_type = input_model.type
        if hasattr(input_model, "options") and isinstance(input_model.options, list) and input_model.options:
            literal_string = f"Literal{input_model.options}"
            # 安全：`options` 来自受控输入，避免注入风险
            field_type = eval(literal_string, {"Literal": Literal})  # noqa: S307
        if hasattr(input_model, "is_list") and input_model.is_list:
            field_type = list[field_type]  # type: ignore[valid-type]
        if input_model.name:
            name = input_model.name.replace("_", " ").title()
        elif input_model.display_name:
            name = input_model.display_name
        else:
            msg = "Input name or display_name is required"
            raise ValueError(msg)
        field_dict = {
            "title": name,
            "description": input_model.info or "",
        }
        if input_model.required is False:
            field_dict["default"] = input_model.value  # type: ignore[assignment]
        pydantic_field = Field(**field_dict)

        fields[input_model.name] = (field_type, pydantic_field)

    # 实现：可选将字段包裹为内层模型
    if param_key is not None:
        # 实现：先构建内层模型
        inner_model = create_model("InnerModel", **fields)

        # 实现：将内层模型挂在 `param_key` 下
        model = create_model("InputSchema", **{param_key: (inner_model, ...)})
    else:
        # 实现：直接构建输入 schema
        model = create_model("InputSchema", **fields)

    model.model_rebuild()
    return model
