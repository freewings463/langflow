"""
模块名称：向量库连接输出装饰器

本模块提供类装饰器，为组件类注入向量库连接输出与便捷方法。主要功能包括：
- 自动扩展 `outputs`，增加向量库连接输出
- 注入 `as_vector_store` 方法，返回构建的向量库实例

关键组件：
- `vector_store_connection`

设计背景：部分组件需要直接输出向量库连接以供下游复用。
使用场景：在图中暴露向量库连接作为独立输出口。
注意事项：仅在类具备 `outputs` 与 `build_vector_store` 时生效。
"""

from langchain_core.vectorstores import VectorStore

from lfx.io import Output


def vector_store_connection(cls):
    """为组件类注入向量库连接输出

    契约：输入类 `cls`，输出增强后的类；副作用：修改类属性 `outputs` 与方法 `as_vector_store`；
    失败语义：若 `outputs` 不存在则仅标记 `decorated`，不抛错。
    关键路径（三步）：
    1) 标记 `decorated` 2) 扩展 `outputs` 添加连接输出 3) 注入 `as_vector_store` 方法。
    决策：通过装饰器而非继承扩展输出口。
    问题：多种组件需要一致的向量库连接输出。
    方案：装饰器统一注入输出与方法。
    代价：运行时修改类属性，调试成本略升高。
    重评：当框架提供统一的输出注册机制时。
    """
    cls.decorated = True

    if hasattr(cls, "outputs"):
        cls.outputs = cls.outputs.copy()
        output_names = [output.name for output in cls.outputs]

        if "vectorstoreconnection" not in output_names:
            cls.outputs.extend(
                [
                    Output(
                        display_name="Vector Store Connection",
                        hidden=False,
                        name="vectorstoreconnection",
                        method="as_vector_store",
                        group_outputs=False,
                    )
                ]
            )

    def as_vector_store(self) -> VectorStore:
        """返回向量库连接实例

        契约：调用 `build_vector_store` 并返回 `VectorStore`；
        副作用：可能触发外部连接或索引构建；
        失败语义：`build_vector_store` 异常透传。
        关键路径：直接调用构建方法。
        决策：复用既有构建逻辑，不引入新接口。
        问题：需要暴露向量库连接供下游使用。
        方案：提供便捷方法返回构建结果。
        代价：可能重复触发构建（若未缓存）。
        重评：当连接对象可缓存或由框架统一提供时。
        """
        return self.build_vector_store()

    cls.as_vector_store = as_vector_store

    return cls
