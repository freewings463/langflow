"""
模块名称：基础链组件模型

本模块定义 `LFX` 链式组件的最小契约，用于被具体链式组件继承。
主要功能包括：
- 约定 `trace_type` 为 `chain`，以统一追踪分类
- 声明必须的输出端 `text`，并绑定 `invoke_chain` 入口
- 提供输出校验逻辑，确保运行前契约完整

关键组件：
- LCChainComponent：链式组件基类，负责输出契约与校验

设计背景：将链式组件的公共约束集中在基础类，减少各实现重复校验。
注意事项：子类必须实现 `invoke_chain`，否则校验阶段会抛 `ValueError`。
"""

from lfx.custom.custom_component.component import Component
from lfx.template.field.base import Output


class LCChainComponent(Component):
    """`LFX` 链式组件的基础抽象。

    契约：子类需提供 `invoke_chain` 方法并暴露名为 `text` 的输出端。
    副作用：无；仅依赖实例上的 `outputs` 与方法定义。
    失败语义：缺少输出或方法时抛出 `ValueError`，调用方应在组件注册阶段修复。
    """

    trace_type = "chain"

    outputs = [Output(display_name="Text", name="text", method="invoke_chain")]

    def _validate_outputs(self) -> None:
        """校验输出契约是否满足 chain 组件要求。

        输入：无（读取 `self.outputs` 与实例方法）。
        输出：无；仅在失败时抛错。
        失败语义：当缺少 `invoke_chain` 输出或方法时抛 `ValueError`。
        """
        required_output_methods = ["invoke_chain"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)
