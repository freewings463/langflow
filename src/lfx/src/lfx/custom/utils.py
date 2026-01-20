"""
模块名称：自定义组件工具函数

本模块提供自定义组件模板构建、字段推断、依赖分析与加载的核心工具函数。
主要功能：
- 构建组件模板与实例；
- 处理输入字段与输出类型；
- 管理组件加载与缓存元数据。

设计背景：集中封装自定义组件处理逻辑，减少分散实现导致的不一致。
注意事项：涉及动态执行与反射，需确保调用方已完成安全校验。
"""

# mypy: ignore-errors  # 注意：该文件存在动态执行与反射，关闭 mypy 检查。
from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import re
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel

from lfx.custom import validate
from lfx.custom.custom_component.component import Component
from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.custom.dependency_analyzer import analyze_component_dependencies
from lfx.custom.directory_reader.utils import (
    abuild_custom_component_list_from_path,
    build_custom_component_list_from_path,
    merge_nested_dicts_with_renaming,
)
from lfx.custom.eval import eval_custom_component_code
from lfx.custom.schema import MissingDefault
from lfx.field_typing.range_spec import RangeSpec
from lfx.helpers.custom import format_type
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict
from lfx.template.field.base import Input
from lfx.template.frontend_node.custom_components import ComponentFrontendNode, CustomComponentFrontendNode
from lfx.type_extraction.type_extraction import extract_inner_type
from lfx.utils.util import get_base_classes

if TYPE_CHECKING:
    from uuid import UUID

    from lfx.custom.custom_component.custom_component import CustomComponent


def _generate_code_hash(source_code: str, modname: str) -> str:
    """生成组件源码哈希

    契约：返回源码的 SHA256 短哈希（前 12 位）。
    异常流：源码为空或类型错误时抛异常。
    """
    if not isinstance(source_code, str):
        msg = "Source code must be a string"
        raise TypeError(msg)

    if not source_code:
        msg = f"Empty source code for {modname}"
        raise ValueError(msg)

    # 实现：生成源码的 SHA256 哈希，取前 12 位用于简短标识。
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()[:12]


class UpdateBuildConfigError(Exception):
    """构建配置更新异常。"""
    pass


def add_output_types(frontend_node: CustomComponentFrontendNode, return_types: list[str]) -> None:
    """向前端节点追加输出类型

    契约：输入返回类型列表；遇到非法类型抛 HTTP 400。
    """
    for return_type in return_types:
        if return_type is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": ("Invalid return type. Please check your code and try again."),
                    "traceback": traceback.format_exc(),
                },
            )
        if return_type is str:
            return_type_ = "Text"
        elif hasattr(return_type, "__name__"):
            return_type_ = return_type.__name__
        elif hasattr(return_type, "__class__"):
            return_type_ = return_type.__class__.__name__
        else:
            return_type_ = str(return_type)

        frontend_node.add_output_type(return_type_)


def reorder_fields(frontend_node: CustomComponentFrontendNode, field_order: list[str]) -> None:
    """按指定顺序重排字段

    契约：字段不存在时跳过；未指定顺序时不做处理。
    """
    if not field_order:
        return

    # 注意：用字典实现 O(1) 字段查找。
    field_dict = {field.name: field for field in frontend_node.template.fields}
    reordered_fields = [field_dict[name] for name in field_order if name in field_dict]
    # 注意：追加未在排序列表中的字段。
    reordered_fields.extend(field for field in frontend_node.template.fields if field.name not in field_order)
    frontend_node.template.fields = reordered_fields
    frontend_node.field_order = field_order


def add_base_classes(frontend_node: CustomComponentFrontendNode, return_types: list[str]) -> None:
    """向前端节点追加输出基类信息。"""
    for return_type_instance in return_types:
        if return_type_instance is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": ("Invalid return type. Please check your code and try again."),
                    "traceback": traceback.format_exc(),
                },
            )

        base_classes = get_base_classes(return_type_instance)
        if return_type_instance is str:
            base_classes.append("Text")

        for base_class in base_classes:
            frontend_node.add_base_class(base_class)


def extract_type_from_optional(field_type):
    """从 Optional[...] 中提取真实类型

    契约：若非 Optional 直接返回原字符串。
    """
    if "optional" not in field_type.lower():
        return field_type
    match = re.search(r"\[(.*?)\]$", field_type)
    return match[1] if match else field_type


def get_field_properties(extra_field):
    """解析额外字段的属性信息

    契约：返回 (name, type, value, required)。
    关键路径：1) 读取 name/type/default 2) 判断必填 3) 可选解析默认值。
    """
    field_name = extra_field["name"]
    field_type = extra_field.get("type", "str")
    field_value = extra_field.get("default", "")
    # 注意：必填字段需满足：类型非 Optional 且无默认值。
    field_required = "optional" not in field_type.lower() and isinstance(field_value, MissingDefault)
    field_value = field_value if not isinstance(field_value, MissingDefault) else None

    if not field_required:
        field_type = extract_type_from_optional(field_type)
    if field_value is not None:
        with contextlib.suppress(Exception):
            field_value = ast.literal_eval(field_value)
    return field_name, field_type, field_value, field_required


def process_type(field_type: str):
    """规范化字段类型

    契约：列表类型提取 inner type；Prompt/Code 类型转小写。
    """
    if field_type.startswith(("list", "List")):
        return extract_inner_type(field_type)

    # 注意：Prompt/Code 类型需转为小写以匹配内部约定。
    lowercase_type = field_type.lower()
    if lowercase_type in {"prompt", "code"}:
        return lowercase_type
    return field_type


def add_new_custom_field(
    *,
    frontend_node: CustomComponentFrontendNode,
    field_name: str,
    field_type: str,
    field_value: Any,
    field_required: bool,
    field_config: dict,
):
    """向前端节点添加新的自定义字段

    契约：根据 field_config 构建 Input 并插入模板；返回更新后的节点。
    关键路径：1) 合并配置 2) 规范化类型 3) 创建 Input 4) 写入模板。
    """
    # 注意：field_config 中存在覆盖项时需优先使用。
    display_name = field_config.pop("display_name", None)
    if not field_type:
        if "type" in field_config and field_config["type"] is not None:
            field_type = field_config.pop("type")
        elif "field_type" in field_config and field_config["field_type"] is not None:
            field_type = field_config.pop("field_type")
    field_contains_list = "list" in field_type.lower()
    field_type = process_type(field_type)
    field_value = field_config.pop("value", field_value)
    field_advanced = field_config.pop("advanced", False)

    if field_type == "Dict":
        field_type = "dict"

    if field_type == "bool" and field_value is None:
        field_value = False

    if field_type == "SecretStr":
        field_config["password"] = True
        field_config["load_from_db"] = True
        field_config["input_types"] = ["Text"]

    # 注意：options 为 list 时视为下拉/多选；为 None 时视为字符串列表。
    is_list = isinstance(field_config.get("options"), list)
    field_config["is_list"] = is_list or field_config.get("list", False) or field_contains_list

    if "name" in field_config:
        logger.warning("The 'name' key in field_config is used to build the object and can't be changed.")
    required = field_config.pop("required", field_required)
    placeholder = field_config.pop("placeholder", "")

    new_field = Input(
        name=field_name,
        field_type=field_type,
        value=field_value,
        show=True,
        required=required,
        advanced=field_advanced,
        placeholder=placeholder,
        display_name=display_name,
        **sanitize_field_config(field_config),
    )
    frontend_node.template.upsert_field(field_name, new_field)
    if isinstance(frontend_node.custom_fields, dict):
        frontend_node.custom_fields[field_name] = None

    return frontend_node


def add_extra_fields(frontend_node, field_config, function_args) -> None:
    """根据函数签名添加额外字段

    契约：仅在函数参数中存在字段时追加；kwargs 场景补齐额外配置。
    """
    if not function_args:
        return
    field_config_ = field_config.copy()
    function_args_names = [arg["name"] for arg in function_args]
    # 注意：若签名包含 kwargs 且存在额外配置字段，则补充额外字段。

    for extra_field in function_args:
        if "name" not in extra_field or extra_field["name"] in {
            "self",
            "kwargs",
            "args",
        }:
            continue

        field_name, field_type, field_value, field_required = get_field_properties(extra_field)
        config = field_config_.pop(field_name, {})
        frontend_node = add_new_custom_field(
            frontend_node=frontend_node,
            field_name=field_name,
            field_type=field_type,
            field_value=field_value,
            field_required=field_required,
            field_config=config,
        )
    if "kwargs" in function_args_names and not all(key in function_args_names for key in field_config):
        for field_name, config in field_config_.items():
            if "name" not in config or field_name == "code":
                continue
            config_ = config.model_dump() if isinstance(config, BaseModel) else config
            field_name_, field_type, field_value, field_required = get_field_properties(extra_field=config_)
            frontend_node = add_new_custom_field(
                frontend_node=frontend_node,
                field_name=field_name_,
                field_type=field_type,
                field_value=field_value,
                field_required=field_required,
                field_config=config_,
            )


def get_field_dict(field: Input | dict):
    """将 Input 或 dict 统一为 dict。"""
    if isinstance(field, Input):
        return dotdict(field.model_dump(by_alias=True, exclude_none=True))
    return field


def run_build_inputs(
    custom_component: Component,
):
    """运行组件的 build_inputs 并返回结果

    契约：失败时抛 HTTP 500 并记录日志。
    """
    try:
        return custom_component.build_inputs()
        # 注意：预留扩展点，必要时可启用额外字段填充。
    except Exception as exc:
        logger.exception("Error running build inputs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def get_component_instance(custom_component: CustomComponent | Component, user_id: str | UUID | None = None):
    """获取组件实例（必要时动态构造）

    契约：返回 Component/CustomComponent 实例；失败抛 HTTP 400。
    关键路径：1) 校验代码类型 2) 动态创建类 3) 实例化。
    """
    # 注意：快速路径避免重复字符串比较。

    code = custom_component._code
    if not isinstance(code, str):
    # 注意：仅处理 None 或非字符串两类错误。
        error = "Code is None" if code is None else "Invalid code type"
        msg = f"Invalid type conversion: {error}. Please check your code and try again."
        logger.error(msg)
        raise HTTPException(status_code=400, detail={"error": msg})

    # 注意：仅在失败时生成完整 traceback，减少正常路径开销。
    try:
        custom_class = eval_custom_component_code(code)
    except Exception as exc:
        # 注意：仅在异常时生成 traceback。
        tb = traceback.format_exc()
        logger.error("Error while evaluating custom component code\n%s", tb)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid type conversion. Please check your code and try again.",
                "traceback": tb,
            },
        ) from exc

    try:
        return custom_class(_user_id=user_id, _code=code)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Error while instantiating custom component\n%s", tb)
        # 注意：仅在 detail 中包含 traceback 时记录。
        detail_tb = getattr(exc, "detail", {}).get("traceback", None)
        if detail_tb is not None:
            logger.error(detail_tb)
        raise


def is_a_preimported_component(custom_component: CustomComponent):
    """判断组件是否为预导入组件。"""
    klass = type(custom_component)
    # 注意：避免重复类型查找，提高常见路径性能。
    return issubclass(klass, Component) and klass is not Component


def run_build_config(
    custom_component: CustomComponent,
    user_id: str | UUID | None = None,
) -> tuple[dict, CustomComponent]:
    """构建组件字段配置

    契约：返回 (build_config, component_instance)；失败抛 HTTP 400。
    关键路径：1) 预导入组件快速路径 2) 动态实例化 3) 处理 RangeSpec。
    """
    # 注意：仅当实例类是 Component 子类时走预导入路径。
    # 注意：预导入组件与路径加载组件的处理路径不同。
    if is_a_preimported_component(custom_component):
        return custom_component.build_config(), custom_component

    if custom_component._code is None:
        error = "Code is None"
    elif not isinstance(custom_component._code, str):
        error = "Invalid code type"
    else:
        try:
            custom_class = eval_custom_component_code(custom_component._code)
        except Exception as exc:
            logger.exception("Error while evaluating custom component code")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": ("Invalid type conversion. Please check your code and try again."),
                    "traceback": traceback.format_exc(),
                },
            ) from exc

        try:
            custom_instance = custom_class(_user_id=user_id)
            build_config: dict = custom_instance.build_config()

            for field_name, field in build_config.copy().items():
                # 注意：允许字段以 Input 或等价 dict 表达。
                field_dict = get_field_dict(field)
                # 注意：rangeSpec 需转换为可序列化结构。
                if "rangeSpec" in field_dict and isinstance(field_dict["rangeSpec"], RangeSpec):
                    field_dict["rangeSpec"] = field_dict["rangeSpec"].model_dump()
                build_config[field_name] = field_dict

        except Exception as exc:
            logger.exception("Error while building field config")
            if hasattr(exc, "detail") and "traceback" in exc.detail:
                logger.error(exc.detail["traceback"])
            raise
        return build_config, custom_instance

    msg = f"Invalid type conversion: {error}. Please check your code and try again."
    logger.error(msg)
    raise HTTPException(
        status_code=400,
        detail={"error": msg},
    )


def add_code_field(frontend_node: CustomComponentFrontendNode, raw_code):
    """向模板追加 code 字段。"""
    code_field = Input(
        dynamic=True,
        required=True,
        placeholder="",
        multiline=True,
        value=raw_code,
        password=False,
        name="code",
        advanced=True,
        field_type="code",
        is_list=False,
    )
    frontend_node.template.add_field(code_field)

    return frontend_node


def add_code_field_to_build_config(build_config: dict, raw_code: str):
    """向 build_config 写入 code 字段。"""
    build_config["code"] = Input(
        dynamic=True,
        required=True,
        placeholder="",
        multiline=True,
        value=raw_code,
        password=False,
        name="code",
        advanced=True,
        field_type="code",
        is_list=False,
    ).model_dump()
    return build_config


def get_module_name_from_display_name(display_name: str):
    """由展示名生成模块名（snake_case）。"""
    # 注意：display_name 转为 snake_case 模块名，例如 "Custom Component" -> "custom_component"。
    # 注意：移除多余空格并转小写。
    cleaned_name = re.sub(r"\s+", " ", display_name.strip())
    # 注意：空格替换为下划线并转小写。
    module_name = cleaned_name.replace(" ", "_").lower()
    # 注意：移除除下划线外的非字母数字字符。
    return re.sub(r"[^a-z0-9_]", "", module_name)


def build_custom_component_template_from_inputs(
    custom_component: Component | CustomComponent, user_id: str | UUID | None = None, module_name: str | None = None
):
    # 注意：Inputs 列表同时承担 build_config 与 entrypoint_args 的角色。
    """基于 inputs 构建前端模板

    契约：返回 (frontend_node_dict, component_instance)。
    关键路径：1) 构建输入字段 2) 添加 code 字段 3) 推断输出类型并校验。
    """
    ctype_name = custom_component.__class__.__name__
    if ctype_name in _COMPONENT_TYPE_NAMES:
        cc_instance = get_component_instance(custom_component, user_id=user_id)

        field_config = cc_instance.get_template_config(cc_instance)
        frontend_node = ComponentFrontendNode.from_inputs(**field_config)

    else:
        frontend_node = ComponentFrontendNode.from_inputs(**custom_component.template_config)
        cc_instance = custom_component
    frontend_node = add_code_field(frontend_node, custom_component._code)
    # 注意：根据输出方法推断返回类型。
    for output in frontend_node.outputs:
        if output.types:
            continue
        return_types = cc_instance.get_method_return_type(output.method)
        return_types = [format_type(return_type) for return_type in return_types]
        output.add_types(return_types)

    # 注意：校验输入与输出名称不冲突。
    frontend_node.validate_component()
    # ! This should be removed when we have a better way to handle this
    frontend_node.set_base_classes_from_outputs()
    reorder_fields(frontend_node, cc_instance._get_field_order())
    frontend_node = build_component_metadata(frontend_node, cc_instance, module_name, ctype_name)

    return frontend_node.to_dict(keep_name=False), cc_instance


def build_component_metadata(
    frontend_node: CustomComponentFrontendNode, custom_component: CustomComponent, module_name: str, ctype_name: str
):
    """构建组件元数据

    契约：写入 module/code_hash/dependencies 等元信息并返回节点。
    异常流：依赖分析失败时填充空依赖信息。
    """
    if module_name:
        frontend_node.metadata["module"] = module_name
    else:
        module_name = get_module_name_from_display_name(frontend_node.display_name)
        frontend_node.metadata["module"] = f"custom_components.{module_name}"

    # 注意：生成代码哈希用于缓存失效与排障。
    try:
        code_hash = _generate_code_hash(custom_component._code, module_name)
        if code_hash:
            frontend_node.metadata["code_hash"] = code_hash
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Error generating code hash for {custom_component.__class__.__name__}", exc_info=exc)

    # 注意：分析组件依赖并写入元数据。
    try:
        dependency_info = analyze_component_dependencies(custom_component._code)
        frontend_node.metadata["dependencies"] = dependency_info
    except (SyntaxError, TypeError, ValueError, ImportError) as exc:
        logger.warning(f"Failed to analyze dependencies for component {ctype_name}: {exc}")
        # 注意：失败时写入最小依赖信息。
        frontend_node.metadata["dependencies"] = {
            "total_dependencies": 0,
            "dependencies": [],
        }

    return frontend_node


def build_custom_component_template(
    custom_component: CustomComponent,
    user_id: str | UUID | None = None,
    module_name: str | None = None,
) -> tuple[dict[str, Any], CustomComponent | Component]:
    """构建组件模板与实例

    契约：返回 (template_dict, component_instance)；失败抛 HTTP 400。
    关键路径：1) 判断输入型/模板型 2) 构建字段与输出 3) 生成元数据。
    """
    try:
        has_template_config = hasattr(custom_component, "template_config")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": (f"Error building Component: {exc}"),
                "traceback": traceback.format_exc(),
            },
        ) from exc
    if not has_template_config:
        raise HTTPException(
            status_code=400,
            detail={
                "error": ("Error building Component. Please check if you are importing Component correctly."),
            },
        )
    try:
        if "inputs" in custom_component.template_config:
            return build_custom_component_template_from_inputs(
                custom_component, user_id=user_id, module_name=module_name
            )
        frontend_node = CustomComponentFrontendNode(**custom_component.template_config)

        field_config, custom_instance = run_build_config(
            custom_component,
            user_id=user_id,
        )

        entrypoint_args = custom_component.get_function_entrypoint_args

        add_extra_fields(frontend_node, field_config, entrypoint_args)

        frontend_node = add_code_field(frontend_node, custom_component._code)

        add_base_classes(frontend_node, custom_component._get_function_entrypoint_return_type)
        add_output_types(frontend_node, custom_component._get_function_entrypoint_return_type)

        reorder_fields(frontend_node, custom_instance._get_field_order())

        if module_name:
            frontend_node = build_component_metadata(
                frontend_node, custom_component, module_name, custom_component.__class__.__name__
            )

        return frontend_node.to_dict(keep_name=False), custom_instance
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=400,
            detail={
                "error": (f"Error building Component: {exc}"),
                "traceback": traceback.format_exc(),
            },
        ) from exc


def create_component_template(
    component: dict | None = None,
    component_extractor: Component | CustomComponent | None = None,
    module_name: str | None = None,
):
    """从组件字典或实例构建模板

    契约：返回 (template, instance)；缺失输出类型时回填。
    """
    component_output_types = []
    if component_extractor is None and component is not None:
        component_code = component["code"]
        component_output_types = component["output_types"]

        component_extractor = Component(_code=component_code)

    component_template, component_instance = build_custom_component_template(
        component_extractor, module_name=module_name
    )
    if not component_template["output_types"] and component_output_types:
        component_template["output_types"] = component_output_types

    return component_template, component_instance


def build_custom_components(components_paths: list[str]):
    """从路径构建自定义组件（同步）。"""
    if not components_paths:
        return {}

    logger.info(f"Building custom components from {components_paths}")

    custom_components_from_file: dict = {}
    processed_paths = set()
    for path in components_paths:
        path_str = str(path)
        if path_str in processed_paths:
            continue

        custom_component_dict = build_custom_component_list_from_path(path_str)
        if custom_component_dict:
            category = next(iter(custom_component_dict))
            logger.debug(f"Loading {len(custom_component_dict[category])} component(s) from category {category}")
            custom_components_from_file = merge_nested_dicts_with_renaming(
                custom_components_from_file, custom_component_dict
            )
        processed_paths.add(path_str)

    return custom_components_from_file


async def abuild_custom_components(components_paths: list[str]):
    """从路径构建自定义组件（异步）。"""
    if not components_paths:
        return {}

    await logger.adebug(f"Building custom components from {components_paths}")
    custom_components_from_file: dict = {}
    processed_paths = set()
    for path in components_paths:
        path_str = str(path)
        if path_str in processed_paths:
            continue

        custom_component_dict = await abuild_custom_component_list_from_path(path_str)
        if custom_component_dict:
            category = next(iter(custom_component_dict))
            await logger.adebug(f"Loading {len(custom_component_dict[category])} component(s) from category {category}")
            custom_components_from_file = merge_nested_dicts_with_renaming(
                custom_components_from_file, custom_component_dict
            )
        processed_paths.add(path_str)

    return custom_components_from_file


def sanitize_field_config(field_config: dict | Input):
    """清理字段配置中不允许覆盖的键。"""
    # 注意：移除不允许用户覆盖的字段键。
    field_dict = field_config.to_dict() if isinstance(field_config, Input) else field_config
    for key in [
        "name",
        "field_type",
        "value",
        "required",
        "placeholder",
        "display_name",
        "advanced",
        "show",
    ]:
        field_dict.pop(key, None)

    # 注意：field_type/type 已在上游提取，避免重复。
    field_dict.pop("field_type", None)
    field_dict.pop("type", None)

    return field_dict


def build_component(component):
    """构建单个组件模板。"""
    component_template, component_instance = create_component_template(component)
    component_name = get_instance_name(component_instance)
    return component_name, component_template


def get_function(code):
    """从代码中构造函数对象。"""
    function_name = validate.extract_function_name(code)

    return validate.create_function(code, function_name)


def get_instance_name(instance):
    """获取实例显示名称。"""
    name = instance.__class__.__name__
    if hasattr(instance, "name") and instance.name:
        name = instance.name
    return name


async def update_component_build_config(
    component: CustomComponent,
    build_config: dotdict,
    field_value: Any,
    field_name: str | None = None,
):
    """更新组件构建配置（兼容同步/异步）。"""
    if inspect.iscoroutinefunction(component.update_build_config):
        return await component.update_build_config(build_config, field_value, field_name)
    return await asyncio.to_thread(component.update_build_config, build_config, field_value, field_name)


async def get_all_types_dict(components_paths: list[str]):
    """获取完整组件类型字典（异步）。"""
    # 注意：这是同步函数的异步版本。
    return await abuild_custom_components(components_paths=components_paths)


async def get_single_component_dict(component_type: str, component_name: str, components_paths: list[str]):
    """按类型与名称加载单个组件模板。"""
    # 注意：示例路径：按 Python 模块方式加载组件。
    for base_path in components_paths:
        module_path = Path(base_path) / component_type / f"{component_name}.py"
        if module_path.exists():
            # 注意：尝试导入模块以获取模板。
            module_name = f"lfx.components.{component_type}.{component_name}"
            try:
                # 注意：这里为简化示例，实际实现可能不同。
                import importlib.util

                spec = importlib.util.spec_from_file_location(module_name, module_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if hasattr(module, "template"):
                        return module.template
            except ImportError as e:
                await logger.aerror(f"Import error loading component {module_path}: {e!s}")
            except AttributeError as e:
                await logger.aerror(f"Attribute error loading component {module_path}: {e!s}")
            except ValueError as e:
                await logger.aerror(f"Value error loading component {module_path}: {e!s}")
            except (KeyError, IndexError) as e:
                await logger.aerror(f"Data structure error loading component {module_path}: {e!s}")
            except RuntimeError as e:
                await logger.aerror(f"Runtime error loading component {module_path}: {e!s}")
                await logger.adebug("Full traceback for runtime error", exc_info=True)
            except OSError as e:
                await logger.aerror(f"OS error loading component {module_path}: {e!s}")

    # 注意：走到这里说明未找到或加载失败。
    return None


async def load_custom_component(component_name: str, components_paths: list[str]):
    """按名称加载自定义组件模板

    契约：返回组件模板 dict 或 None；失败记录日志。
    """
    from lfx.interface.custom_component import get_custom_component_from_name

    try:
        # 注意：优先从已注册组件中查找。
        component_class = get_custom_component_from_name(component_name)
        if component_class:
            # 注意：未导入时在局部定义辅助函数。
            def get_custom_component_template(component_cls):
                """获取自定义组件类的模板。"""
                # 注意：简化实现，必要时可调整。
                if hasattr(component_cls, "get_template"):
                    return component_cls.get_template()
                if hasattr(component_cls, "template"):
                    return component_cls.template
                return None

            return get_custom_component_template(component_class)

        # 注意：注册表未找到时，按路径搜索组件文件。
        for path in components_paths:
            # 注意：在不同分类目录中查找组件。
            base_path = Path(path)
            if base_path.exists() and base_path.is_dir():
                # 注意：遍历子目录以定位组件文件。
                for category_dir in base_path.iterdir():
                    if category_dir.is_dir():
                        component_file = category_dir / f"{component_name}.py"
                        if component_file.exists():
                            # 注意：尝试导入模块并读取模板。
                            module_name = f"lfx.components.{category_dir.name}.{component_name}"
                            try:
                                import importlib.util

                                spec = importlib.util.spec_from_file_location(module_name, component_file)
                                if spec and spec.loader:
                                    module = importlib.util.module_from_spec(spec)
                                    spec.loader.exec_module(module)
                                    if hasattr(module, "template"):
                                        return module.template
                                    if hasattr(module, "get_template"):
                                        return module.get_template()
                            except ImportError as e:
                                await logger.aerror(f"Import error loading component {component_file}: {e!s}")
                                await logger.adebug("Import error traceback", exc_info=True)
                            except AttributeError as e:
                                await logger.aerror(f"Attribute error loading component {component_file}: {e!s}")
                                await logger.adebug("Attribute error traceback", exc_info=True)
                            except (ValueError, TypeError) as e:
                                await logger.aerror(f"Value/Type error loading component {component_file}: {e!s}")
                                await logger.adebug("Value/Type error traceback", exc_info=True)
                            except (KeyError, IndexError) as e:
                                await logger.aerror(f"Data structure error loading component {component_file}: {e!s}")
                                await logger.adebug("Data structure error traceback", exc_info=True)
                            except RuntimeError as e:
                                await logger.aerror(f"Runtime error loading component {component_file}: {e!s}")
                                await logger.adebug("Runtime error traceback", exc_info=True)
                            except OSError as e:
                                await logger.aerror(f"OS error loading component {component_file}: {e!s}")
                                await logger.adebug("OS error traceback", exc_info=True)

    except ImportError as e:
        await logger.aerror(f"Import error loading custom component {component_name}: {e!s}")
        return None
    except AttributeError as e:
        await logger.aerror(f"Attribute error loading custom component {component_name}: {e!s}")
        return None
    except ValueError as e:
        await logger.aerror(f"Value error loading custom component {component_name}: {e!s}")
        return None
    except (KeyError, IndexError) as e:
        await logger.aerror(f"Data structure error loading custom component {component_name}: {e!s}")
        return None
    except RuntimeError as e:
        await logger.aerror(f"Runtime error loading custom component {component_name}: {e!s}")
        logger.debug("Full traceback for runtime error", exc_info=True)
        return None

    # 注意：所有路径都未找到目标组件。
    await logger.awarning(f"Component {component_name} not found in any of the provided paths")
    return None


_COMPONENT_TYPE_NAMES = {"Component", "CustomComponent"}
