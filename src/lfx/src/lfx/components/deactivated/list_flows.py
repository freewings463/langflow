"""
模块名称：流程列表组件（已停用）

本模块提供列出可用 flow 的能力，主要用于旧流程中动态选择子流程。主要功能包括：
- 异步获取当前可用 flow 列表

关键组件：
- `ListFlowsComponent`：流程列表组件

设计背景：在早期 UI 中用于展示/选择已保存的 flow。
注意事项：依赖 `alist_flows` 接口的可用性与权限配置。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.schema.data import Data


class ListFlowsComponent(CustomComponent):
    """流程列表组件。

    契约：返回 `Data` 列表，每条包含 flow 基础信息。
    失败语义：上游 `alist_flows` 异常会向上抛出。
    副作用：更新组件 `status`。
    """
    display_name = "List Flows"
    description = "A component to list all available flows."
    icon = "ListFlows"
    beta: bool = True
    name = "ListFlows"

    def build_config(self):
        """返回空配置。

        契约：该组件无需额外输入。
        失败语义：无。
        副作用：无。
        """
        return {}

    async def build(
        self,
    ) -> list[Data]:
        """异步获取并返回 flow 列表。

        契约：返回 `alist_flows` 的结果。
        失败语义：调用失败时抛异常。
        副作用：更新组件 `status`。
        """
        flows = await self.alist_flows()
        self.status = flows
        return flows
