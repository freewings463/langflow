"""
模块名称：向量库组件基类

本模块提供面向 LFX 组件体系的向量库抽象，统一构建、检索与输出的行为。主要功能包括：
- 规范向量库构建与搜索输出的组件契约
- 提供单次组件执行内的向量库缓存
- 封装检索结果到 `Data`/`DataFrame` 的转换

关键组件：
- `check_cached_vector_store`：向量库构建缓存装饰器
- `LCVectorStoreComponent`：向量库组件基类

设计背景：不同向量库实现需要统一的组件入口，避免重复封装。
使用场景：检索类组件在运行时构建向量库并执行相似度搜索。
注意事项：缓存仅在同一次组件执行内有效，跨运行不持久。
"""

from abc import abstractmethod
from functools import wraps
from typing import TYPE_CHECKING, Any

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Text, VectorStore
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import BoolInput
from lfx.io import HandleInput, Output, QueryInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame

if TYPE_CHECKING:
    from langchain_core.documents import Document


def check_cached_vector_store(f):
    """向量库构建缓存装饰器

    契约：包装 `build_vector_store`，若命中缓存则直接返回；副作用：写入 `_cached_vector_store`；
    失败语义：被包装函数异常原样上抛。
    关键路径：1) 读取 `should_cache_vector_store` 2) 命中缓存返回 3) 生成并写入缓存。
    决策：仅缓存单次组件执行内的向量库实例。
    问题：同一组件多个输出方法会重复构建向量库。
    方案：将构建结果缓存到实例字段。
    代价：增加内存占用，跨执行无法复用。
    重评：当引入跨执行缓存或统一工厂时。
    """

    @wraps(f)
    def check_cached(self, *args, **kwargs):
        should_cache = getattr(self, "should_cache_vector_store", True)

        if should_cache and self._cached_vector_store is not None:
            return self._cached_vector_store

        result = f(self, *args, **kwargs)
        self._cached_vector_store = result
        return result

    check_cached.is_cached_vector_store_checked = True
    return check_cached


class LCVectorStoreComponent(Component):
    """向量库组件基类

    契约：输入 `ingest_data`/`search_query`/`should_cache_vector_store` 等；输出 `list[Data]` 与 `DataFrame`；
    副作用：更新 `self.status`，可能写入 `_cached_vector_store`；
    失败语义：`build_vector_store` 未实现抛 `NotImplementedError`，输入校验失败抛 `ValueError`。
    关键路径：1) 构建/复用向量库 2) 执行检索 3) 结果转换为 `Data`/`DataFrame`。
    决策：在组件层统一处理缓存与输出格式。
    问题：不同向量库实现输出口径不一致且构建成本高。
    方案：提供基类封装并要求子类仅实现构建。
    代价：子类需遵循基类约束与装饰器规则。
    重评：当向量库接口完全统一或缓存下沉到更底层时。
    """

    # 注意：仅保证一次组件执行内共享同一向量库实例
    _cached_vector_store: VectorStore | None = None

    def __init_subclass__(cls, **kwargs):
        """校验子类构建方法是否启用缓存装饰器

        契约：若子类定义 `build_vector_store` 且未使用装饰器则抛 `TypeError`；
        副作用：无；失败语义：违反约束直接阻止类创建。
        关键路径：1) 读取子类方法 2) 校验装饰器标记 3) 失败则抛错。
        决策：在类创建阶段强制缓存装饰器存在。
        问题：漏加装饰器会导致重复构建与性能退化。
        方案：在 `__init_subclass__` 中做强校验。
        代价：子类定义更严格，灵活性降低。
        重评：当缓存策略可自动注入或不再需要时。
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "build_vector_store"):
            method = cls.build_vector_store
            if not hasattr(method, "is_cached_vector_store_checked"):
                msg = (
                    f"The method 'build_vector_store' in class {cls.__name__} "
                    "must be decorated with @check_cached_vector_store"
                )
                raise TypeError(msg)

    trace_type = "retriever"

    inputs = [
        HandleInput(
            name="ingest_data",
            display_name="Ingest Data",
            input_types=["Data", "DataFrame"],
            is_list=True,
        ),
        QueryInput(
            name="search_query",
            display_name="Search Query",
            info="Enter a query to run a similarity search.",
            placeholder="Enter a query...",
            tool_mode=True,
        ),
        BoolInput(
            name="should_cache_vector_store",
            display_name="Cache Vector Store",
            value=True,
            advanced=True,
            info="If True, the vector store will be cached for the current build of the component. "
            "This is useful for components that have multiple output methods and want to share the same vector store.",
        ),
    ]

    outputs = [
        Output(
            display_name="Search Results",
            name="search_results",
            method="search_documents",
        ),
        Output(display_name="DataFrame", name="dataframe", method="as_dataframe"),
    ]

    def _validate_outputs(self) -> None:
        """校验输出方法与输出声明一致性

        契约：要求 `outputs` 中存在 `search_documents` 与 `build_vector_store`；
        副作用：无；失败语义：缺失时抛 `ValueError`。
        关键路径：1) 收集输出名 2) 逐项校验 3) 失败抛错。
        决策：在运行前做显式校验而非隐式失败。
        问题：输出声明缺失会导致运行时行为不一致。
        方案：启动时验证并快速失败。
        代价：增加启动检查开销。
        重评：当输出由框架自动生成并可静态校验时。
        """
        required_output_methods = [
            "search_documents",
            "build_vector_store",
        ]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    def _prepare_ingest_data(self) -> list[Any]:
        """准备需要写入向量库的输入数据

        契约：接收 `ingest_data`（`Data`/`DataFrame` 或其列表）并输出 `list`；
        副作用：无；失败语义：输入为空返回空列表。
        关键路径（三步）：
        1) 规范为列表 2) 将 `DataFrame` 展开为 `Data` 3) 合并输出
        决策：在基类中统一处理 `DataFrame` 展开。
        问题：不同调用方可能传入 `DataFrame` 或 `Data` 混合。
        方案：在入口统一转换，降低子类复杂度。
        代价：增加一次遍历与展开开销。
        重评：当统一输入格式或引入批量转换工具时。
        """
        ingest_data: list | Data | DataFrame = self.ingest_data
        if not ingest_data:
            return []

        if not isinstance(ingest_data, list):
            ingest_data = [ingest_data]

        result = []

        for _input in ingest_data:
            if isinstance(_input, DataFrame):
                result.extend(_input.to_data_list())
            else:
                result.append(_input)
        return result

    def search_with_vector_store(
        self,
        input_value: Text,
        search_type: str,
        vector_store: VectorStore,
        k=10,
        **kwargs,
    ) -> list[Data]:
        """基于向量库执行检索并返回 `Data` 列表

        契约：输入 `input_value`/`search_type`/`vector_store`/`k`；输出 `list[Data]`；
        副作用：更新 `self.status`；失败语义：输入不合法抛 `ValueError`，向量库异常透传。
        关键路径（三步）：
        1) 校验输入与向量库接口 2) 调用向量库 `search` 3) 文档转 `Data` 并写入状态
        决策：依赖向量库统一的 `search` 接口。
        问题：需要在组件层屏蔽不同向量库的文档类型差异。
        方案：统一转为 `Data` 并返回。
        代价：转换增加一次遍历与对象分配。
        重评：当向量库直接返回 `Data` 或统一上游接口时。
        """
        docs: list[Document] = []
        if input_value and isinstance(input_value, str) and hasattr(vector_store, "search"):
            docs = vector_store.search(query=input_value, search_type=search_type.lower(), k=k, **kwargs)
        else:
            msg = "Invalid inputs provided."
            raise ValueError(msg)
        data = docs_to_data(docs)
        self.status = data
        return data

    def search_documents(self) -> list[Data]:
        """执行检索并返回 `Data` 列表

        契约：读取 `search_query`/`search_type`/`number_of_results`；输出 `list[Data]`；
        副作用：构建或复用向量库、更新 `self.status`、记录日志；
        失败语义：`search_query` 为空返回空列表，其它异常透传。
        关键路径（三步）：
        1) 构建或复用向量库 2) 校验查询并记录日志 3) 调用 `search_with_vector_store`。
        决策：优先复用 `_cached_vector_store`。
        问题：同次执行多次检索会重复构建向量库。
        方案：命中缓存直接复用。
        代价：缓存占用内存且不跨执行复用。
        重评：当向量库构建成本下降或引入外部缓存时。
        """
        if self._cached_vector_store is not None:
            vector_store = self._cached_vector_store
        else:
            vector_store = self.build_vector_store()
            self._cached_vector_store = vector_store

        search_query: str = self.search_query
        if not search_query:
            self.status = ""
            return []

        self.log(f"Search input: {search_query}")
        self.log(f"Search type: {self.search_type}")
        self.log(f"Number of results: {self.number_of_results}")

        search_results = self.search_with_vector_store(
            search_query, self.search_type, vector_store, k=self.number_of_results
        )
        self.status = search_results
        return search_results

    def as_dataframe(self) -> DataFrame:
        """将检索结果包装为 `DataFrame`

        契约：调用 `search_documents` 并返回 `DataFrame`；
        副作用：继承 `search_documents` 的状态更新与日志；
        失败语义：下游异常透传。
        关键路径：1) 运行检索 2) 构造 `DataFrame`。
        决策：提供表格化输出以适配下游节点。
        问题：部分节点只接受表格结构。
        方案：在组件层提供 `DataFrame` 输出方法。
        代价：额外对象分配。
        重评：当下游统一使用 `Data` 或改为流式接口时。
        """
        return DataFrame(self.search_documents())

    def get_retriever_kwargs(self):
        """返回检索器扩展参数

        契约：默认返回空字典；子类可覆盖以注入检索参数；
        副作用：无；失败语义：无。
        关键路径：直接返回参数字典。
        决策：提供可覆盖的扩展点而非强制参数字段。
        问题：不同向量库检索参数差异较大。
        方案：允许子类按需返回自定义参数。
        代价：调用方需自行处理参数一致性。
        重评：当参数模型统一或改为结构化配置时。
        """
        return {}

    @abstractmethod
    @check_cached_vector_store
    def build_vector_store(self) -> VectorStore:
        """构建向量库实例（由子类实现）

        契约：返回 `VectorStore` 实例；副作用：可能建立外部连接或索引；
        失败语义：未实现抛 `NotImplementedError`，连接/构建异常透传。
        关键路径：子类创建并返回具体向量库。
        决策：不提供默认实现以避免隐藏依赖。
        问题：不同向量库初始化参数差异大。
        方案：要求子类显式构建。
        代价：子类实现成本增加。
        重评：当引入统一工厂与配置体系时。
        """
        msg = "build_vector_store method must be implemented."
        raise NotImplementedError(msg)
