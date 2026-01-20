"""
模块名称：Composio 工具总入口组件

本模块提供 Composio 工具选择、连接校验与工具构建逻辑，用于在 Langflow 中按动作暴露工具。
主要功能包括：
- 管理 Composio API Key 与实体连接
- 根据工具/动作选择动态构建 LangChain `Tool`
- 在 Astra Cloud 环境中禁止使用并给出明确错误

关键组件：
- ComposioAPIComponent：工具选择、连接检查与构建的核心组件

设计背景：统一 Composio 工具接入流程，减少各应用组件重复逻辑。
注意事项：工具列表当前由 `enabled_tools` 白名单控制；构建工具会触发网络请求。
"""

from collections.abc import Sequence
from typing import Any

from composio import Composio
from composio_langchain import LangchainProvider

from langchain_core.tools import Tool

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.inputs.inputs import (
    ConnectionInput,
    MessageTextInput,
    SecretStrInput,
    SortableListInput,
)
from lfx.io import Output
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

# 注意：工具列表后续计划改为从 API 获取后过滤；当前使用白名单控制
enabled_tools = ["confluence", "discord", "dropbox", "github", "gmail", "linkedin", "notion", "slack", "youtube"]

disable_component_in_astra_cloud_msg = (
    "Composio tools are not supported in Astra cloud environment. "
    "Please use local storage mode or cloud-based versions of the tools."
)


class ComposioAPIComponent(LCToolComponent):
    """Composio 工具选择与构建组件。

    契约：必须提供有效 `api_key`；输出为 `tools` 列表。
    副作用：会访问 Composio API 并更新 UI 构建配置。
    失败语义：鉴权/网络异常会记录日志并以异常透传或降级返回。
    """

    display_name: str = "Composio Tools"
    description: str = "Use Composio toolset to run actions with your agent"
    name = "ComposioAPI"
    icon = "Composio"
    documentation: str = "https://docs.composio.dev"

    inputs = [
        MessageTextInput(name="entity_id", display_name="Entity ID", value="default", advanced=True),
        SecretStrInput(
            name="api_key",
            display_name="Composio API Key",
            required=True,
            info="Refer to https://docs.composio.dev/faq/api_key/api_key",
            real_time_refresh=True,
        ),
        ConnectionInput(
            name="tool_name",
            display_name="Tool Name",
            placeholder="Select a tool...",
            button_metadata={"icon": "unplug", "variant": "destructive"},
            options=[],
            search_category=[],
            value="",
            connection_link="",
            info="The name of the tool to use",
            real_time_refresh=True,
        ),
        SortableListInput(
            name="actions",
            display_name="Actions",
            placeholder="Select action",
            helper_text="Please connect before selecting actions.",
            helper_text_metadata={"icon": "OctagonAlert", "variant": "destructive"},
            options=[],
            value="",
            info="The actions to use",
            limit=1,
            show=False,
        ),
    ]

    outputs = [
        Output(name="tools", display_name="Tools", method="build_tool"),
    ]

    def validate_tool(self, build_config: dict, field_value: Any, tool_name: str | None = None) -> dict:
        """验证工具连接并刷新可用动作列表。

        关键路径（三步）：
        1) 标记当前工具为已验证并更新 UI 文案。
        2) 调用 Composio API 获取已认证动作。
        3) 写回动作选项并显示动作列表。

        异常流：API/属性错误时记录日志并返回空动作列表。
        副作用：修改 `build_config` 内的 `tool_name/actions` 字段。
        """
        selected_tool_index = next(
            (
                ind
                for ind, tool in enumerate(build_config["tool_name"]["options"])
                if tool["name"] == field_value
                or ("validate" in field_value and tool["name"] == field_value["validate"])
            ),
            None,
        )

        build_config["tool_name"]["options"][selected_tool_index]["link"] = "validated"

        build_config["actions"]["helper_text"] = ""
        build_config["actions"]["helper_text_metadata"] = {"icon": "Check", "variant": "success"}

        try:
            composio = self._build_wrapper()
            current_tool = tool_name or getattr(self, "tool_name", None)
            if not current_tool:
                self.log("No tool name available for validate_tool")
                return build_config

            toolkit_slug = current_tool.lower()

            tools = composio.tools.get(user_id=self.entity_id, toolkits=[toolkit_slug])

            authenticated_actions = []
            for tool in tools:
                if hasattr(tool, "name"):
                    action_name = tool.name
                    display_name = action_name.replace("_", " ").title()
                    authenticated_actions.append({"name": action_name, "display_name": display_name})
        except (ValueError, ConnectionError, AttributeError) as e:
            self.log(f"Error getting actions for {current_tool or 'unknown tool'}: {e}")
            authenticated_actions = []

        build_config["actions"]["options"] = [
            {
                "name": action["name"],
            }
            for action in authenticated_actions
        ]

        build_config["actions"]["show"] = True
        return build_config

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据字段变化更新组件构建配置。

        关键路径（三步）：
        1) 当 API Key 变化时刷新工具列表或清空状态。
        2) 当工具变更时检查连接状态并尝试 OAuth。
        3) 根据连接结果更新校验状态与动作列表。

        异常流：网络/属性异常仅记录日志，不中断返回。
        副作用：修改 `build_config` 以驱动前端 UI 状态。
        """
        if field_name == "api_key" or (self.api_key and not build_config["tool_name"]["options"]):
            if field_name == "api_key" and not field_value:
                build_config["tool_name"]["options"] = []
                build_config["tool_name"]["value"] = ""

                build_config["actions"]["show"] = False
                build_config["actions"]["options"] = []
                build_config["actions"]["value"] = ""

                return build_config

            build_config["tool_name"]["options"] = [
                {
                    "name": app.title(),
                    "icon": app,
                    "link": (
                        build_config["tool_name"]["options"][ind]["link"]
                        if build_config["tool_name"]["options"]
                        else ""
                    ),
                }
                for ind, app in enumerate(enabled_tools)
            ]

            return build_config

        if field_name == "tool_name" and field_value:
            composio = self._build_wrapper()

            current_tool_name = (
                field_value
                if isinstance(field_value, str)
                else field_value.get("validate")
                if isinstance(field_value, dict) and "validate" in field_value
                else getattr(self, "tool_name", None)
            )

            if not current_tool_name:
                self.log("No tool name available for connection check")
                return build_config

            try:
                toolkit_slug = current_tool_name.lower()

                connection_list = composio.connected_accounts.list(
                    user_ids=[self.entity_id], toolkit_slugs=[toolkit_slug]
                )

                has_active_connections = False
                if (
                    connection_list
                    and hasattr(connection_list, "items")
                    and connection_list.items
                    and isinstance(connection_list.items, list)
                    and len(connection_list.items) > 0
                ):
                    for connection in connection_list.items:
                        if getattr(connection, "status", None) == "ACTIVE":
                            has_active_connections = True
                            break

                selected_tool_index = next(
                    (
                        ind
                        for ind, tool in enumerate(build_config["tool_name"]["options"])
                        if tool["name"] == current_tool_name.title()
                    ),
                    None,
                )

                if has_active_connections:
                    if selected_tool_index is not None:
                        build_config["tool_name"]["options"][selected_tool_index]["link"] = "validated"

                    if (isinstance(field_value, dict) and "validate" in field_value) or isinstance(field_value, str):
                        return self.validate_tool(build_config, field_value, current_tool_name)
                else:
                    try:
                        connection = composio.toolkits.authorize(user_id=self.entity_id, toolkit=toolkit_slug)
                        redirect_url = getattr(connection, "redirect_url", None)

                        if redirect_url and redirect_url.startswith(("http://", "https://")):
                            if selected_tool_index is not None:
                                build_config["tool_name"]["options"][selected_tool_index]["link"] = redirect_url
                        elif selected_tool_index is not None:
                            build_config["tool_name"]["options"][selected_tool_index]["link"] = "error"
                    except (ValueError, ConnectionError, AttributeError) as e:
                        self.log(f"Error creating OAuth connection: {e}")
                        if selected_tool_index is not None:
                            build_config["tool_name"]["options"][selected_tool_index]["link"] = "error"

            except (ValueError, ConnectionError, AttributeError) as e:
                self.log(f"Error checking connection status: {e}")

        return build_config

    def build_tool(self) -> Sequence[Tool]:
        """按动作列表构建 Composio 工具实例。

        关键路径（三步）：
        1) 校验环境并构建 Composio 客户端。
        2) 从动作名推导工具包集合。
        3) 拉取工具并按动作名过滤。

        输入：无（读取 `self.actions`/`self.api_key` 等配置）。
        输出：`Sequence[Tool]`，仅包含用户选择的动作。
        失败语义：Astra Cloud 环境直接抛 `ValueError`；鉴权异常由 `_build_wrapper` 透传。
        """
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        composio = self._build_wrapper()
        action_names = [action["name"] for action in self.actions]

        toolkits = set()
        for action_name in action_names:
            if "_" in action_name:
                toolkit = action_name.split("_")[0].lower()
                toolkits.add(toolkit)

        if not toolkits:
            return []

        all_tools = composio.tools.get(user_id=self.entity_id, toolkits=list(toolkits))

        return [tool for tool in all_tools if hasattr(tool, "name") and tool.name in action_names]

    def _build_wrapper(self) -> Composio:
        """构建 Composio SDK 客户端。

        输入：无（读取 `self.api_key`）。
        输出：`Composio` 客户端实例。
        失败语义：缺少或无效 API Key 抛 `ValueError`，并提示设置方式。
        """
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        try:
            if not self.api_key:
                msg = "Composio API Key is required"
                raise ValueError(msg)
            return Composio(api_key=self.api_key, provider=LangchainProvider())
        except ValueError as e:
            self.log(f"Error building Composio wrapper: {e}")
            msg = "Please provide a valid Composio API Key in the component settings"
            raise ValueError(msg) from e
