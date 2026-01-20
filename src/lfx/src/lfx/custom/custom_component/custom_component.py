"""模块名称：自定义组件核心实现

本模块提供自定义组件的核心实现与通用运行逻辑，包括配置构建、数据转换、变量读取与运行控制。
主要功能包括：加载/运行 Flow、解析输入、生成前端配置、处理变量与路径。

关键组件：
- `CustomComponent`：自定义组件核心基类

设计背景：为用户自定义组件提供统一的运行时行为与开发接口。
注意事项：部分方法依赖图上下文与存储服务，调用前需确保 graph/vertex 就绪。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml
from cachetools import TTLCache
from langchain_core.documents import Document
from pydantic import BaseModel

from lfx.custom import validate
from lfx.custom.custom_component.base_component import BaseComponent
from lfx.helpers import (
    get_flow_by_id_or_name,
    list_flows,
    list_flows_by_flow_folder,
    list_flows_by_folder_id,
    load_flow,
    run_flow,
)
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.services.deps import get_storage_service, get_variable_service, session_scope
from lfx.services.storage.service import StorageService
from lfx.template.utils import update_frontend_node_with_template_values
from lfx.type_extraction import post_process_type
from lfx.utils.async_helpers import run_until_complete

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler

    from lfx.graph.graph.base import Graph
    from lfx.graph.vertex.base import Vertex
    from lfx.schema.dotdict import dotdict
    from lfx.schema.log import Log
    from lfx.schema.schema import OutputValue
    from lfx.services.storage.service import StorageService
    from lfx.services.tracing.service import TracingService


class CustomComponent(BaseComponent):
    """自定义组件核心基类。

    契约：输入自定义组件配置与代码；输出可执行组件行为与前端配置；
    副作用：读取/写入图状态、访问存储与变量服务；失败语义：关键上下文缺失时抛异常。
    关键路径：1) 初始化运行时状态 2) 解析/生成模板配置 3) 执行构建逻辑。
    决策：继承 `BaseComponent` 复用代码解析与入口函数逻辑
    问题：自定义组件需要统一的运行时能力
    方案：在基类中集中管理状态与工具方法
    代价：基类体积大、学习成本高
    重评：当组件体系稳定后拆分为更小的 mixin

    属性说明：
    - `name/display_name/description/icon`：用于前端展示与识别
    - `field_config/field_order`：构建配置与字段顺序
    - `function_entrypoint_name`：入口函数名（默认 `build`）
    - `_tree/_code`：源码解析树与源码字符串
    """

    # 需要跨实例共享的常量（ClassVar）
    _code_class_base_inheritance: ClassVar[str] = "CustomComponent"
    function_entrypoint_name: ClassVar[str] = "build"
    name: str | None = None
    """组件名称（用于前端样式标识），默认 None。"""
    display_name: str | None = None
    """组件显示名称，默认 None。"""
    description: str | None = None
    """组件描述，默认 None。"""
    icon: str | None = None
    """组件图标（通常为 emoji），默认 None。"""
    priority: int | None = None
    """组件在分类中的优先级，值越小越靠前显示，默认 None。"""

    def __init__(self, **data) -> None:
        """初始化自定义组件实例。

        契约：输入关键字参数；输出无；副作用：初始化运行时状态与缓存；
        失败语义：无。
        关键路径：1) 初始化实例字段 2) 初始化集合与缓存 3) 调用父类构造。
        决策：先初始化自身字段再调用父类
        问题：父类构造依赖子类字段默认值
        方案：提前设置运行时属性
        代价：构造逻辑较长
        重评：当父类不依赖子类字段时可简化顺序
        """
        # 先初始化实例级属性
        self.is_input: bool | None = None
        self.is_output: bool | None = None
        self.add_tool_output: bool = False
        self.field_config: dict = {}
        self.field_order: list[str] | None = None
        self.frozen: bool = False
        self.build_parameters: dict | None = None
        self._vertex: Vertex | None = None
        self.function: Callable | None = None
        self.repr_value: Any = ""
        self.status: Any | None = None

        # 初始化集合类字段为默认空值
        self._flows_data: list[Data] | None = None
        self._outputs: list[OutputValue] = []
        self._logs: list[Log] = []
        self._output_logs: dict[str, list[Log] | Log] = {}
        self._tracing_service: TracingService | None = None
        self._tree: dict | None = None

        # 初始化额外运行时状态
        self.cache: TTLCache = TTLCache(maxsize=1024, ttl=60)
        self._results: dict = {}
        self._artifacts: dict = {}
        # 先设置自身属性后再调用父类构造
        super().__init__(**data)

    def set_attributes(self, parameters: dict) -> None:
        pass

    def set_parameters(self, parameters: dict) -> None:
        self._parameters = parameters
        self.set_attributes(self._parameters)

    def get_vertex(self):
        return self._vertex

    def get_results(self):
        return self._results

    def get_artifacts(self):
        return self._artifacts

    def set_results(self, results: dict):
        self._results = results

    def set_artifacts(self, artifacts: dict):
        self._artifacts = artifacts

    @property
    def trace_name(self) -> str:
        if hasattr(self, "_id") and self._id is None:
            msg = "Component id is not set"
            raise ValueError(msg)
        if hasattr(self, "_id"):
            return f"{self.display_name} ({self._id})"
        return f"{self.display_name}"

    def stop(self, output_name: str | None = None) -> None:
        if not output_name and self._vertex and len(self._vertex.outputs) == 1:
            output_name = self._vertex.outputs[0]["name"]
        elif not output_name:
            msg = "You must specify an output name to call stop"
            raise ValueError(msg)
        if not self._vertex:
            msg = "Vertex is not set"
            raise ValueError(msg)
        try:
            self.graph.mark_branch(vertex_id=self._vertex.id, output_name=output_name, state="INACTIVE")
        except Exception as e:
            msg = f"Error stopping {self.display_name}: {e}"
            raise ValueError(msg) from e

    def start(self, output_name: str | None = None) -> None:
        if not output_name and self._vertex and len(self._vertex.outputs) == 1:
            output_name = self._vertex.outputs[0]["name"]
        elif not output_name:
            msg = "You must specify an output name to call start"
            raise ValueError(msg)
        if not self._vertex:
            msg = "Vertex is not set"
            raise ValueError(msg)
        try:
            self.graph.mark_branch(vertex_id=self._vertex.id, output_name=output_name, state="ACTIVE")
        except Exception as e:
            msg = f"Error starting {self.display_name}: {e}"
            raise ValueError(msg) from e

    @staticmethod
    def resolve_path(path: str) -> str:
        """将路径解析为绝对路径。

        契约：输入路径字符串；输出绝对路径字符串；副作用：无；
        失败语义：无（空值直接返回）。
        关键路径：1) 处理 `~` 2) 解析相对路径 3) 返回字符串。
        决策：仅处理 `~` 与相对路径
        问题：保证用户输入路径可用于文件系统访问
        方案：使用 `Path.expanduser/resolve`
        代价：可能触发文件系统访问
        重评：当需要沙箱路径时加入路径白名单
        """
        if not path:
            return path
        path_object = Path(path)

        if path_object.parts and path_object.parts[0] == "~":
            path_object = path_object.expanduser()
        elif path_object.is_relative_to("."):
            path_object = path_object.resolve()
        return str(path_object)

    def get_full_path(self, path: str) -> str:
        storage_svc: StorageService = get_storage_service()

        flow_id, file_name = path.split("/", 1)
        return storage_svc.build_full_path(flow_id, file_name)

    @property
    def graph(self):
        return self._vertex.graph

    @property
    def user_id(self):
        if hasattr(self, "_user_id") and self._user_id:
            return self._user_id
        return self.graph.user_id

    @property
    def flow_id(self):
        return self.graph.flow_id

    @property
    def flow_name(self):
        return self.graph.flow_name

    @property
    def tracing_service(self):
        """延迟初始化 tracing 服务。"""
        if self._tracing_service is None:
            from lfx.services.deps import get_tracing_service

            try:
                self._tracing_service = get_tracing_service()
            except Exception:  # noqa: BLE001
                # 使用宽泛异常是有意为之，用于容错处理服务初始化失败
                self._tracing_service = None
        return self._tracing_service

    def _get_field_order(self):
        return self.field_order or list(self.field_config.keys())

    def get_field_order(self):
        """获取字段顺序。

        契约：输入无；输出字段名列表；副作用无；
        失败语义：无。
        关键路径：1) 返回 `_get_field_order` 结果。
        决策：优先使用显式 `field_order`
        问题：保证前端字段展示顺序可控
        方案：提供可覆盖的方法
        代价：需要维护字段顺序配置
        重评：当前端自动排序稳定时可移除显式顺序
        """
        return self._get_field_order()

    def get_function_entrypoint_return_type(self) -> list[Any]:
        """获取入口函数返回类型。

        契约：输入无；输出类型列表；副作用无；失败语义：无。
        关键路径：1) 返回 `_get_function_entrypoint_return_type`。
        决策：统一返回列表结构
        问题：前端需要稳定的类型集合
        方案：封装在属性方法中返回
        代价：需要解析类型信息
        重评：当类型解析由上层处理时可移除
        """
        return self._get_function_entrypoint_return_type

    def custom_repr(self):
        """生成自定义展示字符串。

        契约：输入无；输出字符串；副作用：可能更新 `repr_value`；
        失败语义：无。
        关键路径：1) 选择 repr 值 2) 进行类型分支转换 3) 返回字符串。
        决策：当 `repr_value` 为空时回退到 `status`
        问题：需要统一的可视化文本
        方案：按类型输出 YAML/字符串
        代价：复杂对象可能被简化
        重评：当需要富文本时扩展格式化逻辑
        """
        if self.repr_value == "":
            self.repr_value = self.status
        if isinstance(self.repr_value, dict):
            return yaml.dump(self.repr_value)
        if isinstance(self.repr_value, str):
            return self.repr_value
        if isinstance(self.repr_value, BaseModel) and not isinstance(self.repr_value, Data):
            return str(self.repr_value)
        return self.repr_value

    def build_config(self):
        """构建组件配置。

        契约：输入无；输出配置字典；副作用无；失败语义：无。
        关键路径：1) 返回 `field_config`。
        决策：直接返回当前配置
        问题：保持配置源单一
        方案：不做深拷贝
        代价：调用方修改会影响原配置
        重评：当需要不可变配置时改为深拷贝
        """
        return self.field_config

    def update_build_config(
        self,
        build_config: dotdict,
        field_value: Any,
        field_name: str | None = None,
    ):
        """更新构建配置（可被子类重写）。

        契约：输入 `build_config/field_value/field_name`；输出更新后的配置；
        副作用：更新字段值；失败语义：字段缺失将触发 `KeyError`。
        关键路径：1) 写入字段值 2) 返回配置。
        决策：仅更新目标字段
        问题：保持更新逻辑最小化
        方案：直接写入 `build_config[field_name]["value"]`
        代价：不处理复杂联动逻辑
        重评：当需要动态字段联动时由子类实现
        """
        build_config[field_name]["value"] = field_value
        return build_config

    @property
    def tree(self):
        """获取自定义组件代码树。

        契约：输入无；输出解析树字典；副作用：缓存解析结果；
        失败语义：解析失败抛异常。
        关键路径：1) 调用 `get_code_tree`。
        决策：空代码返回空解析树
        问题：需要稳定的解析入口
        方案：当 `_code` 为空时传空字符串
        代价：空代码解析结果为空
        重评：当需要强校验时抛异常
        """
        return self.get_code_tree(self._code or "")

    def to_data(self, data: Any, *, keys: list[str] | None = None, silent_errors: bool = False) -> list[Data]:
        """将输入转换为 `Data` 列表。

        契约：输入任意数据；输出 `list[Data]`；副作用无；
        失败语义：输入类型不支持抛 `TypeError`，缺失键抛 `ValueError`（可静默）。
        关键路径：1) 归一化为序列 2) 按类型解析 3) 组装 `Data` 列表。
        决策：支持 `Document/BaseModel/str/dict`
        问题：上游输入类型不一致
        方案：按类型分支构造统一 `Data`
        代价：复杂对象可能丢失字段
        重评：当需要保留完整结构时扩展映射逻辑
        """
        if not keys:
            keys = []
        data_objects = []
        if not isinstance(data, Sequence):
            data = [data]
        for item in data:
            data_dict = {}
            if isinstance(item, Document):
                data_dict = item.metadata
                data_dict["text"] = item.page_content
            elif isinstance(item, BaseModel):
                model_dump = item.model_dump()
                for key in keys:
                    if silent_errors:
                        data_dict[key] = model_dump.get(key, "")
                    else:
                        try:
                            data_dict[key] = model_dump[key]
                        except KeyError as e:
                            msg = f"Key {key} not found in {item}"
                            raise ValueError(msg) from e

            elif isinstance(item, str):
                data_dict = {"text": item}
            elif isinstance(item, dict):
                data_dict = item.copy()
            else:
                msg = f"Invalid data type: {type(item)}"
                raise TypeError(msg)

            data_objects.append(Data(data=data_dict))

        return data_objects

    def get_method_return_type(self, method_name: str):
        build_method = self.get_method(method_name)
        if not build_method or not build_method.get("has_return"):
            return []
        return_type = build_method["return_type"]

        return self._extract_return_type(return_type)

    def create_references_from_data(self, data: list[Data], *, include_data: bool = False) -> str:
        """从 `Data` 列表生成引用文本。

        契约：输入 `list[Data]`；输出 markdown 字符串；副作用无；
        失败语义：无（空列表返回空字符串）。
        关键路径：1) 遍历数据 2) 拼接文本与可选 data 3) 返回 markdown。
        决策：使用 `---` 作为分隔头
        问题：需要统一引用格式
        方案：按行拼接 `Text/Data`
        代价：格式较简单
        重评：当需要富格式引用时改为模板化
        """
        if not data:
            return ""
        markdown_string = "---\n"
        for value in data:
            markdown_string += f"- Text: {value.get_text()}"
            if include_data:
                markdown_string += f" Data: {value.data}"
            markdown_string += "\n"
        return markdown_string

    @property
    def get_function_entrypoint_args(self) -> list:
        """获取入口函数参数列表。

        契约：输入无；输出参数列表；副作用无；
        失败语义：入口方法缺失时返回空列表。
        关键路径：1) 读取入口方法 2) 补全缺失类型为 `Data`。
        决策：缺失类型默认 `Data`
        问题：前端需要默认类型以生成输入
        方案：对无类型参数强制补齐
        代价：可能与真实类型不一致
        重评：当类型注解完整时移除默认补齐
        """
        build_method = self.get_method(self._function_entrypoint_name)
        if not build_method:
            return []

        args = build_method["args"]
        for arg in args:
            if not arg.get("type") and arg.get("name") != "self":
                # 缺失类型时默认补为 Data
                arg["type"] = "Data"
        return args

    def get_method(self, method_name: str):
        """获取指定方法的解析信息。

        契约：输入方法名；输出方法信息字典；副作用无；
        失败语义：无（未找到返回空字典）。
        关键路径：1) 从代码树筛选组件类 2) 从方法列表中匹配。
        决策：默认取首个匹配组件类
        问题：同一代码中可能包含多个组件类
        方案：优先使用第一个匹配类
        代价：多组件场景可能选错
        重评：当支持多组件时引入显式选择
        """
        if not self._code:
            return {}

        component_classes = [
            cls for cls in self.tree["classes"] if "Component" in cls["bases"] or "CustomComponent" in cls["bases"]
        ]
        if not component_classes:
            return {}

        # 默认取第一个匹配的 Component 类
        component_class = component_classes[0]
        build_methods = [method for method in component_class["methods"] if method["name"] == (method_name)]

        return build_methods[0] if build_methods else {}

    @property
    def _get_function_entrypoint_return_type(self) -> list[Any]:
        """获取入口函数返回类型列表。

        契约：输入无；输出类型列表；副作用无；
        失败语义：未解析到类型时返回空列表。
        关键路径：1) 调用 `get_method_return_type`。
        决策：通过解析树获取类型
        问题：运行时无法直接获取用户代码类型
        方案：使用解析树的类型提取
        代价：解析不完整时可能为空
        重评：当运行时类型可获取时改为反射
        """
        return self.get_method_return_type(self._function_entrypoint_name)

    def _extract_return_type(self, return_type: Any) -> list[Any]:
        return post_process_type(return_type)

    @property
    def get_main_class_name(self):
        """获取主组件类名。

        契约：输入无；输出类名字符串；副作用无；
        失败语义：未找到返回空字符串。
        关键路径：1) 遍历类定义 2) 匹配基类与入口方法。
        决策：入口方法名作为主类判定条件
        问题：多个类继承同基类时需识别主类
        方案：匹配包含入口方法的类
        代价：若多个类都满足可能误判
        重评：当支持多组件时改为显式标记
        """
        if not self._code:
            return ""

        base_name = self._code_class_base_inheritance
        method_name = self._function_entrypoint_name

        classes = []
        for item in self.tree.get("classes", []):
            if base_name in item["bases"]:
                method_names = [method["name"] for method in item["methods"]]
                if method_name in method_names:
                    classes.append(item["name"])

        # 仅取第一个匹配项
        return next(iter(classes), "")

    @property
    def template_config(self):
        """获取模板配置（带缓存）。

        契约：输入无；输出模板配置字典；副作用：可能构建并缓存模板；
        失败语义：构建失败抛异常。
        关键路径：1) 若无缓存则构建 2) 返回缓存结果。
        决策：惰性构建模板配置
        问题：避免每次访问都重建配置
        方案：缓存到 `_template_config`
        代价：配置变更需手动刷新
        重评：当配置需实时更新时移除缓存
        """
        if not self._template_config:
            self._template_config = self.build_template_config()
        return self._template_config

    def variables(self, name: str, field: str):
        """已废弃：兼容旧接口，推荐使用 `get_variables`。

        契约：输入变量名与字段；输出变量值；副作用：可能访问数据库；
        失败语义：变量不存在或用户未设置会抛异常。
        关键路径：1) 直接调用 `get_variables`。
        决策：保留同步封装以兼容旧调用
        问题：历史接口仍被使用
        方案：通过 `run_until_complete` 包装
        代价：同步阻塞
        重评：当旧接口完全移除时删除
        """
        return run_until_complete(self.get_variables(name, field))

    async def get_variables(self, name: str, field: str):
        """已废弃：兼容旧接口，推荐使用 `get_variable`。

        契约：输入变量名与字段；输出变量值；副作用：访问数据库；
        失败语义：变量不存在时抛异常。
        关键路径：1) 获取数据库会话 2) 调用 `get_variable`。
        决策：保持旧签名
        问题：老版本组件仍调用该方法
        方案：在兼容层内转发
        代价：额外一次函数跳转
        重评：当迁移完成后移除
        """
        async with session_scope() as session:
            return await self.get_variable(name, field, session)

    async def get_variable(self, name: str, field: str, session):
        """获取当前用户指定变量。

        契约：输入变量名与字段、数据库会话；输出变量值；
        副作用：访问变量服务与数据库；失败语义：user_id 未设置或变量不存在时抛异常。
        关键路径：1) 检查上下文覆盖 2) 校验 user_id 3) 调用变量服务。
        决策：优先使用 graph context 的 `request_variables`
        问题：无 user_id 时仍需支持 run_flow 场景
        方案：在上下文中查找覆盖变量
        代价：上下文优先可能掩盖数据库变量
        重评：当需要强一致时关闭上下文覆盖
        """
        # 优先检查图上下文中的请求级变量覆盖
        # 允许在未提供 user_id 时仍可通过变量运行 flow
        if hasattr(self, "graph") and self.graph and hasattr(self.graph, "context"):
            context = self.graph.context
            if context and "request_variables" in context:
                request_variables = context["request_variables"]
                if name in request_variables:
                    logger.debug(f"Found context override for variable '{name}'")
                    return request_variables[name]

        # 仅在访问数据库时校验 user_id
        if hasattr(self, "_user_id") and not self.user_id:
            msg = f"User id is not set for {self.__class__.__name__}"
            raise ValueError(msg)

        variable_service = get_variable_service()  # 获取服务实例
        # 按用户查询并解密变量
        if isinstance(self.user_id, str):
            user_id = uuid.UUID(self.user_id)
        elif isinstance(self.user_id, uuid.UUID):
            user_id = self.user_id
        else:
            msg = f"Invalid user id: {self.user_id}"
            raise TypeError(msg)
        return await variable_service.get_variable(user_id=user_id, name=name, field=field, session=session)

    async def list_key_names(self):
        """列出当前用户的变量名。

        契约：输入无；输出变量名列表；副作用：访问数据库；
        失败语义：user_id 未设置时抛异常。
        关键路径：1) 校验 user_id 2) 调用变量服务。
        决策：仅在 user_id 存在时查询
        问题：匿名上下文无法查询用户变量
        方案：缺失时直接报错
        代价：无 user_id 的流程无法列出变量
        重评：当引入匿名变量空间时调整行为
        """
        if hasattr(self, "_user_id") and not self.user_id:
            msg = f"User id is not set for {self.__class__.__name__}"
            raise ValueError(msg)
        variable_service = get_variable_service()

        async with session_scope() as session:
            return await variable_service.list_variables(user_id=self.user_id, session=session)

    def index(self, value: int = 0):
        """返回一个按索引取值的函数。

        契约：输入索引值；输出函数；副作用无；失败语义：索引越界由调用方处理。
        关键路径：1) 构造闭包 2) 读取列表指定索引。
        决策：空列表返回自身
        问题：避免空列表导致异常
        方案：当列表为空返回原对象
        代价：返回类型可能不一致
        重评：当需要严格索引行为时直接抛异常
        """

        def get_index(iterable: list[Any]):
            return iterable[value] if iterable else iterable

        return get_index

    def get_function(self):
        """获取自定义组件入口函数。

        契约：输入无；输出可调用函数；副作用无；
        失败语义：代码或入口名缺失时由下游抛异常。
        关键路径：1) 调用 `validate.create_function`。
        决策：复用 `validate` 的函数创建逻辑
        问题：需要统一的安全构建方式
        方案：委托 validate 模块
        代价：依赖外部实现
        重评：当引入更严格沙箱时替换实现
        """
        return validate.create_function(self._code, self._function_entrypoint_name)

    async def load_flow(self, flow_id: str, tweaks: dict | None = None) -> Graph:
        """加载指定 Flow。

        契约：输入 `flow_id/tweaks`；输出 `Graph`；副作用：访问存储服务；
        失败语义：`user_id` 缺失抛 `ValueError`。
        关键路径：1) 校验用户 2) 调用 `load_flow`。
        决策：要求 `user_id` 存在
        问题：避免匿名访问持久化 Flow
        方案：缺失时直接报错
        代价：匿名环境不可用
        重评：当支持匿名会话时放宽限制
        """
        if not self.user_id:
            msg = "Session is invalid"
            raise ValueError(msg)
        return await load_flow(user_id=str(self.user_id), flow_id=flow_id, tweaks=tweaks)

    async def run_flow(
        self,
        inputs: dict | list[dict] | None = None,
        flow_id: str | None = None,
        flow_name: str | None = None,
        output_type: str | None = "chat",
        tweaks: dict | None = None,
    ) -> Any:
        """执行指定 Flow。

        契约：输入运行参数与 Flow 标识；输出运行结果；副作用：触发 Flow 运行；
        失败语义：底层运行异常透传。
        关键路径：1) 组装参数 2) 调用 `run_flow`。
        决策：使用当前 graph 的 `run_id`
        问题：需要在同一运行上下文中追踪
        方案：传入 `self.graph.run_id`
        代价：依赖 graph 上下文
        重评：当 run_id 不可用时生成新 ID
        """
        return await run_flow(
            inputs=inputs,
            output_type=output_type,
            flow_id=flow_id,
            flow_name=flow_name,
            tweaks=tweaks,
            user_id=str(self.user_id),
            run_id=self.graph.run_id,
        )

    def list_flows(self) -> list[Data]:
        """已废弃：兼容旧接口，推荐使用 `alist_flows`。

        契约：输入无；输出 Flow 列表；副作用：访问存储服务；
        失败语义：运行异常抛 `ValueError`。
        关键路径：1) 同步调用 `alist_flows`。
        决策：保留同步入口
        问题：历史调用仍依赖同步接口
        方案：使用 `run_until_complete` 包装
        代价：同步阻塞
        重评：当旧接口完全移除时删除
        """
        return run_until_complete(self.alist_flows())

    async def alist_flows(self) -> list[Data]:
        """列出当前用户的全部 Flow。

        契约：输入无；输出 `list[Data]`；副作用：访问存储服务；
        失败语义：调用失败抛 `ValueError`。
        关键路径：1) 调用 `list_flows` 2) 包装异常信息。
        决策：错误时统一抛 `ValueError`
        问题：上层需要统一异常类型
        方案：捕获并包装异常
        代价：原始异常类型丢失
        重评：当需要区分异常时保留原类型
        """
        try:  # 用户 id 在函数内部校验
            return await list_flows(user_id=str(self.user_id))
        except Exception as e:
            msg = f"Error listing flows: {e}"
            raise ValueError(msg) from e

    async def alist_flows_by_flow_folder(self) -> list[Data]:
        """列出与当前 Flow 同目录的 Flow 列表。

        契约：输入无；输出 `list[Data]`；副作用：访问存储服务；
        失败语义：调用失败抛 `ValueError`。
        关键路径：1) 获取当前 flow_id 2) 调用 `list_flows_by_flow_folder`。
        决策：未找到 flow_id 时返回空列表
        问题：无上下文时无法定位目录
        方案：空结果作为降级
        代价：可能掩盖配置问题
        重评：当需要显式提示时改为抛错
        """
        flow_id = self._get_runtime_or_frontend_node_attr("flow_id")
        if flow_id is not None:
            try:  # 用户与 flow id 在函数内部校验
                return await list_flows_by_flow_folder(user_id=str(self.user_id), flow_id=str(flow_id))
            except Exception as e:
                msg = f"Error listing flows: {e}"
                raise ValueError(msg) from e
        return []

    async def alist_flows_by_folder_id(self) -> list[Data]:
        """按文件夹 ID 列出 Flow。

        契约：输入无；输出 `list[Data]`；副作用：访问存储服务；
        失败语义：调用失败抛 `ValueError`。
        关键路径：1) 获取 folder_id 2) 调用 `list_flows_by_folder_id`。
        决策：无 folder_id 时返回空列表
        问题：缺少上下文无法查询
        方案：空列表降级
        代价：可能掩盖配置错误
        重评：当需要提示时抛异常
        """
        folder_id = self._get_runtime_or_frontend_node_attr("folder_id")
        if folder_id is not None:
            try:  # 用户与 flow id 在函数内部校验
                return await list_flows_by_folder_id(
                    user_id=str(self.user_id),
                    folder_id=str(folder_id),
                )
            except Exception as e:
                msg = f"Error listing flows: {e}"
                raise ValueError(msg) from e
        return []

    async def aget_flow_by_id_or_name(self) -> Data | None:
        """按 ID 或名称获取 Flow 信息。

        契约：输入无；输出 `Data` 或 `None`；副作用：访问存储服务；
        失败语义：调用失败抛 `ValueError`。
        关键路径：1) 获取 flow_id/flow_name 2) 调用 `get_flow_by_id_or_name`。
        决策：无标识时返回 `None`
        问题：无标识无法查询
        方案：返回空值
        代价：调用方需处理 `None`
        重评：当需要强制标识时改为抛错
        """
        flow_id = self._get_runtime_or_frontend_node_attr("flow_id")
        flow_name = self._get_runtime_or_frontend_node_attr("flow_name")
        if flow_id or flow_name:
            try:  # 用户与 flow id 在函数内部校验
                return await get_flow_by_id_or_name(
                    user_id=str(self.user_id), flow_id=str(flow_id) if flow_id else None, flow_name=flow_name
                )
            except Exception as e:
                msg = f"Error listing flows: {e}"
                raise ValueError(msg) from e
        return None

    def build(self, *args: Any, **kwargs: Any) -> Any:
        """构建组件（由子类实现）。

        契约：输入任意参数；输出任意；副作用未知；
        失败语义：未实现时抛 `NotImplementedError`。
        关键路径：由子类定义。
        决策：保持抽象接口
        问题：强制子类提供构建逻辑
        方案：抛 `NotImplementedError`
        代价：需要实现成本
        重评：当需要默认实现时提供基类实现
        """
        raise NotImplementedError

    def post_code_processing(self, new_frontend_node: dict, current_frontend_node: dict):
        """已废弃：兼容旧接口，推荐使用 `update_frontend_node`。

        契约：输入前端节点字典；输出无；副作用：调用异步更新；
        失败语义：更新失败抛异常。
        关键路径：1) 调用 `update_frontend_node`。
        决策：保留旧接口
        问题：历史代码仍调用该方法
        方案：同步包装异步方法
        代价：同步阻塞
        重评：当旧接口移除时删除
        """
        run_until_complete(self.update_frontend_node(new_frontend_node, current_frontend_node))

    async def update_frontend_node(self, new_frontend_node: dict, current_frontend_node: dict):
        """根据当前节点更新新前端节点配置。

        契约：输入新旧节点字典；输出更新后的节点；副作用无；
        失败语义：无（内部异常透传）。
        关键路径：1) 调用模板更新工具 2) 返回新节点。
        决策：在代码校验后执行
        问题：需要保留用户已有配置
        方案：使用 `update_frontend_node_with_template_values`
        代价：可能覆盖部分字段
        重评：当需要更细粒度合并时自定义策略
        """
        return update_frontend_node_with_template_values(
            frontend_node=new_frontend_node, raw_frontend_node=current_frontend_node
        )

    def get_langchain_callbacks(self) -> list[BaseCallbackHandler]:
        if self.tracing_service and hasattr(self.tracing_service, "get_langchain_callbacks"):
            return self.tracing_service.get_langchain_callbacks()
        return []

    def _get_runtime_or_frontend_node_attr(self, attr_name: str) -> Any:
        """从运行时或前端节点获取属性值。

        契约：输入属性名；输出属性值或 `None`；副作用无；
        失败语义：无。
        关键路径：1) 读取运行时属性 2) 回退到 `_frontend_node_*`。
        决策：优先运行时值
        问题：构建配置阶段需要从前端节点读取
        方案：若运行时为空则回退
        代价：前端值可能过期
        重评：当运行时可靠性提升后取消回退
        """
        value = getattr(self, attr_name, None)
        if value is None:
            attr = f"_frontend_node_{attr_name}"
            if hasattr(self, attr):
                value = getattr(self, attr)
        return value
