"""
模块名称：Composio GoogleTasks 组件

本模块提供 Composio 平台的 GoogleTasks 接入组件，用于在 Langflow 中调用对应工具。
主要功能包括：
- 绑定 `app_name` 为 `googletasks`，以匹配 Composio 工具包标识
- 暴露组件元信息（显示名/图标/文档地址）

关键组件：
- ComposioGoogleTasksAPIComponent：GoogleTasks 的 Composio 组件封装

设计背景：统一通过 ComposioBaseComponent 复用鉴权与工具装配逻辑。
注意事项：如需预置默认动作，可在组件中实现 `set_default_tools`。
"""

from lfx.base.composio.composio_base import ComposioBaseComponent


class ComposioGoogleTasksAPIComponent(ComposioBaseComponent):
    """GoogleTasks 的 Composio 组件封装。

    契约：`app_name` 必须与 Composio 工具包标识一致。
    副作用：无；具体网络调用由基类在执行阶段触发。
    失败语义：鉴权或工具装配异常由基类抛出并透传。
    """

    display_name: str = "GoogleTasks"
    icon = "GoogleTasks"
    documentation: str = "https://docs.composio.dev"
    app_name = "googletasks"
