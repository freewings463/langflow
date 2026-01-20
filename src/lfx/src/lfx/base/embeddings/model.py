"""
模块名称：embeddings.model

本模块定义 Embeddings 组件基类，用于在 Langflow 组件系统中暴露向量输出。
主要功能包括：
- 规范 `build_embeddings` 的抽象接口
- 校验输出声明与实现方法的一致性

关键组件：
- `LCEmbeddingsModel`：Embeddings 组件基类

设计背景：组件系统需要稳定的 embeddings 输出契约
使用场景：自定义 Embeddings 组件继承本类
注意事项：子类必须实现 `build_embeddings`
"""

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Embeddings
from lfx.io import Output


class LCEmbeddingsModel(Component):
    """Embeddings 组件基类。

    契约：子类需实现 `build_embeddings` 并保持输出名一致。
    副作用：通过 `outputs` 暴露组件输出端口。
    失败语义：输出或方法缺失将在校验或调用时抛异常。
    决策：固定 `build_embeddings` 作为输出绑定方法。
    问题：组件系统需要稳定的输出方法名以进行自动绑定。
    方案：在基类中定义输出与方法名约定。
    代价：子类自由度下降，需遵循固定命名。
    重评：当系统支持可配置输出绑定时。
    """

    trace_type = "embedding"

    outputs = [
        Output(display_name="Embedding Model", name="embeddings", method="build_embeddings"),
    ]

    def _validate_outputs(self) -> None:
        """校验输出声明与方法实现的一致性。

        契约：`outputs` 必含名为 `build_embeddings` 的输出项。
        失败语义：缺失输出或方法时抛 `ValueError`。
        排障入口：异常信息包含缺失的名称。
        """
        required_output_methods = ["build_embeddings"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    def build_embeddings(self) -> Embeddings:
        """构建并返回 Embeddings 实例。

        契约：返回实现 `Embeddings` 协议的对象。
        副作用：由子类决定（如网络初始化、缓存预热）。
        失败语义：基类直接抛 `NotImplementedError`。
        决策：基类采用运行时提示而非抽象方法强制。
        问题：部分组件系统在抽象基类下初始化受限。
        方案：在默认实现中显式抛错提示子类实现。
        代价：错误暴露在运行时而非类型检查期。
        重评：当基类可安全改为 `abc.ABC` 时。
        """
        msg = "You must implement the build_embeddings method in your class."
        raise NotImplementedError(msg)
