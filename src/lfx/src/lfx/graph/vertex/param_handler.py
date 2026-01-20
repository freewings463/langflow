"""
模块名称：Vertex 参数处理器

模块目的：将节点模板字段与边连接转换为可执行参数。
使用场景：节点构建前对输入参数进行装配、清洗与类型转换。
主要功能包括：
- 解析边连接参数并映射到节点输入
- 处理模板字段（文件/表格/代码/直接类型）
- 记录需要从数据库加载的字段信息

关键组件：
- `ParameterHandler`：参数解析与装配入口

设计背景：统一参数来源与类型处理逻辑，减少散落在 Vertex 的复杂度。
注意：部分字段会触发存储服务访问与类型转换，需关注副作用。
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

import pandas as pd

from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.services.deps import get_storage_service
from lfx.utils.constants import DIRECT_TYPES
from lfx.utils.util import unescape_string

if TYPE_CHECKING:
    from lfx.graph.edge.base import CycleEdge
    from lfx.graph.vertex.base import Vertex


class ParameterHandler:
    """节点参数处理器。

    契约：输入 `Vertex` 与可选存储服务，输出构建所需参数字典。
    关键路径：`process_edge_parameters` 解析边参数，`process_field_parameters` 解析模板字段。
    决策：将参数解析逻辑集中在此类，避免 Vertex 过度膨胀。
    问题：参数来源多样且类型分支复杂。
    方案：按字段类型分派处理函数。
    代价：新增字段类型需同步维护解析逻辑。
    重评：当字段类型增长过快或需插件化处理时。
    """

    def __init__(self, vertex: Vertex, storage_service) -> None:
        """初始化参数处理器。

        契约：绑定目标 `Vertex` 并提取模板字段字典。
        副作用：可能延迟初始化存储服务。
        异常流：模板结构缺失时会触发 KeyError。
        排障：检查 `vertex.data["node"]["template"]` 是否存在。
        """
        self.vertex = vertex
        self.template_dict: dict[str, dict] = {
            key: value for key, value in vertex.data["node"]["template"].items() if isinstance(value, dict)
        }
        self.params: dict[str, Any] = {}
        self.load_from_db_fields: list[str] = []
        self._storage_service = storage_service
        self._storage_service_initialized = False

    @property
    def storage_service(self):
        """按需初始化存储服务。

        契约：首次访问时创建存储服务实例。
        副作用：调用 `get_storage_service` 获取全局服务。
        异常流：服务不可用时抛异常。
        """
        if not self._storage_service_initialized:
            if self._storage_service is None:
                self._storage_service = get_storage_service()
            self._storage_service_initialized = True
        return self._storage_service

    def process_edge_parameters(self, edges: list[CycleEdge]) -> dict[str, Any]:
        """从边连接解析参数。

        契约：输入边列表，返回边参数字典。
        异常流：不抛异常，无法处理的边会被跳过。
        性能：遍历边列表，复杂度与边数量线性相关。
        排障：检查边是否包含 `target_param`。
        """
        params: dict[str, Any] = {}
        for edge in edges:
            if not hasattr(edge, "target_param"):
                continue
            params = self._set_params_from_normal_edge(params, edge)
        return params

    def _set_params_from_normal_edge(self, params: dict[str, Any], edge: CycleEdge) -> dict[str, Any]:
        """将单条边映射为参数。

        契约：返回更新后的 `params`。
        注意：若目标参数在 `output_names`，视为循环回边处理。
        """
        param_key = edge.target_param

        if param_key in self.template_dict and edge.target_id == self.vertex.id:
            field = self.template_dict[param_key]
            if field.get("list"):
                if param_key not in params:
                    params[param_key] = []
                params[param_key].append(self.vertex.graph.get_vertex(edge.source_id))
            else:
                params[param_key] = self.process_non_list_edge_param(field, edge)
        elif param_key in self.vertex.output_names:
            params[param_key] = self.vertex.graph.get_vertex(edge.source_id)
        return params

    def process_non_list_edge_param(self, field: dict, edge: CycleEdge) -> Any:
        """处理非列表类型的边参数。"""
        param_dict = field.get("value")
        if isinstance(param_dict, dict) and len(param_dict) == 1:
            return {key: self.vertex.graph.get_vertex(edge.source_id) for key in param_dict}
        return self.vertex.graph.get_vertex(edge.source_id)

    def process_field_parameters(self) -> tuple[dict[str, Any], list[str]]:
        """从模板字段解析参数与加载清单。

        契约：返回 `(params, load_from_db_fields)`。
        关键路径：遍历字段 -> 按类型分派处理 -> 处理可选字段。
        异常流：未知字段类型抛 `ValueError`。
        性能：复杂度与字段数量线性相关。
        排障：检查字段 `type` 与 `DIRECT_TYPES` 是否匹配。
        """
        params: dict[str, Any] = {}
        load_from_db_fields: list[str] = []

        for field_name, field in self.template_dict.items():
            if self.should_skip_field(field_name, field, params):
                continue

            if field.get("type") == "file":
                params = self.process_file_field(field_name, field, params)
            elif field.get("type") in DIRECT_TYPES and params.get(field_name) is None:
                params, load_from_db_fields = self._process_direct_type_field(
                    field_name, field, params, load_from_db_fields
                )
            else:
                msg = f"Field {field_name} in {self.vertex.display_name} is not a valid field type: {field.get('type')}"
                raise ValueError(msg)

            self.handle_optional_field(field_name, field, params)

        return params, load_from_db_fields

    def should_skip_field(self, field_name: str, field: dict, params: dict[str, Any]) -> bool:
        """判断字段是否应跳过解析。

        契约：返回 `True` 表示跳过处理。
        注意：`override_skip` 为真时强制不跳过。
        """
        if field.get("override_skip"):
            return False
        return (
            field.get("type") == "other"
            or field_name in params
            or field_name == "_type"
            or (not field.get("show") and field_name != "code")
        )

    def process_file_field(self, field_name: str, field: dict, params: dict[str, Any]) -> dict[str, Any]:
        """处理文件类型字段。

        契约：将逻辑路径转换为可用文件路径并写入 `params`。
        副作用：调用存储服务解析路径。
        异常流：路径解析失败时可能抛 `ValueError`。
        排障：确认 `file_path` 格式与存储服务配置。
        """
        if file_path := field.get("file_path"):
            try:
                full_path: str | list[str] = ""
                if field.get("list"):
                    full_path = []
                    if isinstance(file_path, str):
                        file_path = [file_path]
                    for p in file_path:
                        resolved = self.storage_service.resolve_component_path(p)
                        full_path.append(resolved)
                else:
                    full_path = self.storage_service.resolve_component_path(file_path)

            except ValueError as e:
                if "too many values to unpack" in str(e):
                    full_path = file_path
                else:
                    raise
            params[field_name] = full_path
        elif field.get("required"):
            field_display_name = field.get("display_name")
            logger.warning(
                "File path not found for %s in component %s. Setting to None.",
                field_display_name,
                self.vertex.display_name,
            )
            params[field_name] = None
        elif field["list"]:
            params[field_name] = []
        else:
            params[field_name] = None
        return params

    def _process_direct_type_field(
        self, field_name: str, field: dict, params: dict[str, Any], load_from_db_fields: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        """处理直接类型字段（非文件）。"""
        val = field.get("value")

        if field.get("type") == "code":
            params = self._handle_code_field(field_name, val, params)
        elif field.get("type") in {"dict", "NestedDict"}:
            params = self._handle_dict_field(field_name, val, params)
        elif field.get("type") == "table":
            params = self._handle_table_field(field_name, val, params, load_from_db_fields)
        else:
            params = self._handle_other_direct_types(field_name, field, val, params)

        if field.get("load_from_db"):
            load_from_db_fields.append(field_name)

        return params, load_from_db_fields

    def _handle_table_field(
        self,
        field_name: str,
        val: Any,
        params: dict[str, Any],
        load_from_db_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """处理表格字段并记录需从数据库加载的列。

        契约：表格值必须为 `list[dict]`，否则抛 `ValueError`。
        副作用：在 `params` 中追加 `*_load_from_db_columns` 元数据。
        排障：检查 `table_schema` 与输入表格结构一致性。
        """
        if load_from_db_fields is None:
            load_from_db_fields = []
        if val is None:
            params[field_name] = []
            return params

        if isinstance(val, list) and all(isinstance(item, dict) for item in val):
            params[field_name] = val
        else:
            msg = f"Invalid value type {type(val)} for table field {field_name}"
            raise ValueError(msg)

        field_template = self.template_dict.get(field_name, {})
        table_schema = field_template.get("table_schema", [])

        load_from_db_columns = []
        for column_schema in table_schema:
            if isinstance(column_schema, dict) and column_schema.get("load_from_db"):
                load_from_db_columns.append(column_schema["name"])
            elif hasattr(column_schema, "load_from_db") and column_schema.load_from_db:
                load_from_db_columns.append(column_schema.name)

        if load_from_db_columns:
            table_load_metadata_key = f"{field_name}_load_from_db_columns"
            params[table_load_metadata_key] = load_from_db_columns

            load_from_db_fields.append(f"table:{field_name}")
            self.load_from_db_fields.append(f"table:{field_name}")

        return params

    def handle_optional_field(self, field_name: str, field: dict, params: dict[str, Any]) -> None:
        """处理可选字段默认值。"""
        if not field.get("required") and params.get(field_name) is None:
            if field.get("default"):
                params[field_name] = field.get("default")
            else:
                params.pop(field_name, None)

    def _handle_code_field(self, field_name: str, val: Any, params: dict[str, Any]) -> dict[str, Any]:
        """处理代码字段，必要时进行 `literal_eval`。"""
        try:
            if field_name == "code":
                params[field_name] = val
            else:
                params[field_name] = ast.literal_eval(val) if val else None
        except Exception:  # noqa: BLE001
            logger.debug("Error evaluating code for %s", field_name)
            params[field_name] = val
        return params

    def _handle_dict_field(self, field_name: str, val: Any, params: dict[str, Any]) -> dict[str, Any]:
        """处理字典字段（支持列表聚合）。"""
        match val:
            case list():
                params[field_name] = {k: v for item in val for k, v in item.items()}
            case dict():
                params[field_name] = val
        return params

    def _handle_other_direct_types(
        self, field_name: str, field: dict, val: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        """处理其他直接类型字段（int/float/str/bool/table/tools）。"""
        if val is None:
            return params

        match field.get("type"):
            case "int":
                try:
                    params[field_name] = int(val)
                except ValueError:
                    params[field_name] = val
            case "float" | "slider":
                try:
                    params[field_name] = float(val)
                except ValueError:
                    params[field_name] = val
            case "str":
                match val:
                    case list():
                        params[field_name] = [unescape_string(v) for v in val]
                    case str():
                        params[field_name] = unescape_string(val)
                    case Data():
                        params[field_name] = unescape_string(val.get_text())
            case "bool":
                match val:
                    case bool():
                        params[field_name] = val
                    case str():
                        params[field_name] = bool(val)
            case "table" | "tools":
                if isinstance(val, list) and all(isinstance(item, dict) for item in val):
                    params[field_name] = pd.DataFrame(val)
                else:
                    msg = f"Invalid value type {type(val)} for field {field_name}"
                    raise ValueError(msg)
            case _:
                if val:
                    params[field_name] = val

        return params
