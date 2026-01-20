"""
模块名称：embeddings_class

本模块提供 Embeddings 包装器，支持默认实例与多模型映射。
主要功能包括：
- 保存主 Embeddings 实例作为默认实现
- 维护模型名到实例的映射供上层选择

关键组件：
- `EmbeddingsWithModels`：多模型包装类

设计背景：上层需要在多个 Embeddings 实例之间切换
使用场景：按模型名路由或回退到默认模型
注意事项：包装器不做路由逻辑，仅提供统一访问入口
"""

from langchain_core.embeddings import Embeddings


class EmbeddingsWithModels(Embeddings):
    """携带多模型映射的 Embeddings 包装器。

    契约：`embeddings` 为默认实例，`available_models` 为模型名->实例映射。
    副作用：仅保存底层实例引用，不复制对象。
    失败语义：下游 Embeddings 抛出的异常不拦截。
    决策：使用多实例映射而非运行时切换同一实例配置。
    问题：动态切换可能污染状态并影响并发安全。
    方案：每个模型保持独立实例，由上层显式选择。
    代价：内存与连接数增加。
    重评：当底层 Embeddings 支持无副作用的热切换时。
    """

    def __init__(
        self,
        embeddings: Embeddings,
        available_models: dict[str, Embeddings] | None = None,
    ):
        """初始化多模型包装器。

        契约：`embeddings` 为默认实例；`available_models` 为空则使用空映射。
        副作用：保存实例引用并完成父类初始化。
        失败语义：无显式异常；由入参类型错误导致的异常原样抛出。
        决策：默认保留空映射而非强制要求可用模型列表。
        问题：部分场景仅需单模型，但仍需兼容多模型接口。
        方案：允许 `available_models` 为空并延后填充。
        代价：调用方需自行保证路由时的模型存在性。
        重评：当系统要求强一致模型清单时。
        """
        super().__init__()
        self.embeddings = embeddings
        self.available_models = available_models if available_models is not None else {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """同步生成文档向量，默认走主实例。

        契约：`texts` 为字符串列表；返回与输入等长的向量列表。
        副作用：调用底层实例的同步接口。
        失败语义：下游异常原样抛出。
        决策：不在此处做模型路由。
        问题：保持与 LangChain `Embeddings` 接口一致。
        方案：直接委托 `self.embeddings`。
        代价：调用方需自行按模型名选择实例。
        重评：若需在此方法内引入统一路由策略。
        """
        return self.embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """同步生成查询向量，保持与文档向量同一空间。

        契约：`text` 为单条文本；返回单个向量。
        副作用：调用底层实例的同步接口。
        失败语义：下游异常原样抛出。
        决策：与 `embed_documents` 共用同一默认实例。
        问题：避免查询/文档使用不同模型导致检索失配。
        方案：统一委托到 `self.embeddings`。
        代价：无法在此处做按模型差异化选择。
        重评：若检索流程支持显式模型路由。
        """
        return self.embeddings.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """异步生成文档向量，复用底层 async 实现。

        契约：`texts` 为字符串列表；返回与输入等长的向量列表。
        副作用：在事件循环中调用下游异步接口。
        失败语义：下游异常原样抛出。
        决策：不自建异步 HTTP 客户端。
        问题：保持与提供方原生异步实现一致的限流/重试策略。
        方案：直接 await `self.embeddings.aembed_documents`。
        代价：包装层无法插入统一的异步中间件。
        重评：当需要跨提供方的统一 async 管控时。
        """
        return await self.embeddings.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        """异步生成查询向量，保持调用路径一致。

        契约：`text` 为单条文本；返回单个向量。
        副作用：在事件循环中调用下游异步接口。
        失败语义：下游异常原样抛出。
        决策：沿用底层异步实现而非单独实现。
        问题：避免包装层破坏提供方的超时/重试行为。
        方案：await `self.embeddings.aembed_query`。
        代价：包装层难以插入额外监控。
        重评：当需要统一异步观测能力时。
        """
        return await self.embeddings.aembed_query(text)

    def __call__(self, *args, **kwargs):
        """将可调用语义透传给底层实例。

        契约：仅当 `self.embeddings` 可调用时才透传调用。
        副作用：执行底层实例的 `__call__`。
        失败语义：底层不可调用时抛 `TypeError`。
        决策：保留历史可调用用法以兼容旧代码。
        问题：部分 Embeddings 以函数形式被使用。
        方案：检测 `callable` 后委托执行。
        代价：运行时才暴露不可调用错误。
        重评：当所有调用方迁移为显式方法调用时。
        """
        if callable(self.embeddings):
            return self.embeddings(*args, **kwargs)
        msg = f"'{type(self.embeddings).__name__}' object is not callable"
        raise TypeError(msg)

    def __getattr__(self, name: str):
        """转发未知属性访问到下游实例。

        契约：仅在包装器自身不存在该属性时触发。
        副作用：访问下游实例属性或方法。
        失败语义：若下游不存在该属性，抛 `AttributeError`。
        决策：保持对提供方扩展接口的兼容性。
        问题：不同 Embeddings 可能暴露自定义方法。
        方案：使用 `__getattr__` 透传访问。
        代价：静态类型提示不完整。
        重评：当上层提供显式的扩展接口适配层时。
        """
        return getattr(self.embeddings, name)

    def __repr__(self) -> str:
        """返回包装器的可读字符串表示。

        契约：包含默认实例与可用模型映射的摘要信息。
        副作用：无。
        失败语义：无显式异常。
        决策：输出 `repr` 级信息便于排障。
        问题：运行期需要快速定位当前包装的实例。
        方案：拼接 `embeddings` 与 `available_models` 的 `repr`。
        代价：可能暴露较长的调试信息。
        重评：当日志敏感信息管控要求更严格时。
        """
        return f"EmbeddingsWithModels(embeddings={self.embeddings!r}, available_models={self.available_models!r})"
