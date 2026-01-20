"""JSON Schema 工具函数。"""

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, create_model

from lfx.log.logger import logger

NULLABLE_TYPE_LENGTH = 2  # 可空联合类型数量（类型本身 + null）


def _snake_to_camel(name: str) -> str:
    """将 snake_case 转为 camelCase（保留首尾下划线）。"""
    if not name:
        return name

    # 处理前导下划线
    leading = ""
    start_idx = 0
    while start_idx < len(name) and name[start_idx] == "_":
        leading += "_"
        start_idx += 1

    # 处理结尾下划线
    trailing = ""
    end_idx = len(name)
    while end_idx > start_idx and name[end_idx - 1] == "_":
        trailing += "_"
        end_idx -= 1

    # 转换中间部分
    middle = name[start_idx:end_idx]
    if not middle:
        return name  # 全为下划线

    components = middle.split("_")
    camel = components[0] + "".join(word.capitalize() for word in components[1:])

    return leading + camel + trailing


def create_input_schema_from_json_schema(schema: dict[str, Any]) -> type[BaseModel]:
    """从 JSON Schema 动态构建 Pydantic 模型。

    关键路径（三步）：
    1) 解析 schema 并解析 $ref；
    2) 映射 JSON 类型到 Python 类型；
    3) 创建并返回动态模型。
    """
    if schema.get("type") != "object":
        msg = "Root schema must be type 'object'"
        raise ValueError(msg)

    defs: dict[str, dict[str, Any]] = schema.get("$defs", {})
    model_cache: dict[str, type[BaseModel]] = {}

    def resolve_ref(s: dict[str, Any] | None) -> dict[str, Any]:
        """解析 $ref 链接并返回实际 subschema。"""
        if s is None:
            return {}
        while "$ref" in s:
            ref_name = s["$ref"].split("/")[-1]
            s = defs.get(ref_name)
            if s is None:
                logger.warning(f"Parsing input schema: Definition '{ref_name}' not found")
                return {"type": "string"}
        return s

    def parse_type(s: dict[str, Any] | None) -> Any:
        """将 JSON Schema 类型映射为 Python 类型。"""
        if s is None:
            return None
        s = resolve_ref(s)

        if "anyOf" in s:
            # 处理常见可空类型（anyOf 包含 null）
            subtypes = [sub.get("type") for sub in s["anyOf"] if isinstance(sub, dict) and "type" in sub]

            # 判断是否为简单可空类型
            if len(subtypes) == NULLABLE_TYPE_LENGTH and "null" in subtypes:
                # 获取非 null 类型
                non_null_type = next(t for t in subtypes if t != "null")
                # 映射为 Python 类型
                if isinstance(non_null_type, str):
                    return {
                        "string": str,
                        "integer": int,
                        "number": float,
                        "boolean": bool,
                        "object": dict,
                        "array": list,
                    }.get(non_null_type, Any)
                return Any

            # 其他 anyOf 情况使用首个非空类型
            subtypes = [parse_type(sub) for sub in s["anyOf"]]
            non_null_types = [t for t in subtypes if t is not None and t is not type(None)]
            if non_null_types:
                return non_null_types[0]
            return str

        t = s.get("type", "any")  # 默认使用 "any"
        if t == "array":
            item_schema = s.get("items", {})
            schema_type: Any = parse_type(item_schema)
            return list[schema_type]

        if t == "object":
            # 内联对象创建匿名模型
            return _build_model(f"AnonModel{len(model_cache)}", s)

        # 原子类型回退
        return {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "object": dict,
            "array": list,
        }.get(t, Any)

    def _build_model(name: str, subschema: dict[str, Any]) -> type[BaseModel]:
        """为对象 schema 创建/获取模型类。"""
        # 若来自具名 $ref，则复用名称
        if "$ref" in subschema:
            refname = subschema["$ref"].split("/")[-1]
            if refname in model_cache:
                return model_cache[refname]
            target = defs.get(refname)
            if not target:
                msg = f"Definition '{refname}' not found"
                raise ValueError(msg)
            cls = _build_model(refname, target)
            model_cache[refname] = cls
            return cls

        # 具名匿名模型：避免名称冲突
        if name in model_cache:
            return model_cache[name]

        props = subschema.get("properties", {})
        reqs = set(subschema.get("required", []))
        fields: dict[str, Any] = {}

        for prop_name, prop_schema in props.items():
            py_type = parse_type(prop_schema)
            is_required = prop_name in reqs
            if not is_required:
                py_type = py_type | None
                default = prop_schema.get("default", None)
            else:
                default = ...  # Pydantic 必填

            # snake_case 自动添加 camelCase 别名
            field_kwargs = {"description": prop_schema.get("description")}
            if "_" in prop_name:
                camel_case_name = _snake_to_camel(prop_name)
                if camel_case_name != prop_name:  # 仅在名称不同才添加别名
                    field_kwargs["validation_alias"] = AliasChoices(prop_name, camel_case_name)

            fields[prop_name] = (py_type, Field(default, **field_kwargs))

        model_cls = create_model(name, **fields)
        model_cache[name] = model_cls
        return model_cls

    # 构建顶层 InputSchema
    top_props = schema.get("properties", {})
    top_reqs = set(schema.get("required", []))
    top_fields: dict[str, Any] = {}

    for fname, fdef in top_props.items():
        py_type = parse_type(fdef)
        if fname not in top_reqs:
            py_type = py_type | None
            default = fdef.get("default", None)
        else:
            default = ...

        # snake_case 自动添加 camelCase 别名
        field_kwargs = {"description": fdef.get("description")}
        if "_" in fname:
            camel_case_name = _snake_to_camel(fname)
            if camel_case_name != fname:  # 仅在名称不同才添加别名
                field_kwargs["validation_alias"] = AliasChoices(fname, camel_case_name)

        top_fields[fname] = (py_type, Field(default, **field_kwargs))

    return create_model("InputSchema", **top_fields)
