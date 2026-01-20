"""
模块名称：Flow Tool 组件

本模块提供将已有 Flow 封装为工具的组件实现，主要用于在代理场景中
以工具形式调用其他 Flow。
主要功能包括：
- 查询可用 Flow 列表并获取 Flow 数据
- 将 Flow 构建为可调用的 `FlowTool`

关键组件：
- `FlowToolComponent`：Flow 作为工具的组件封装

设计背景：复用已有 Flow 逻辑并以工具方式对代理暴露。
注意事项：组件已标记为 legacy，推荐使用替代组件。
"""

from typing import Any

from typing_extensions import override

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.base.tools.flow_tool import FlowTool
from lfx.field_typing import Tool
from lfx.graph.graph.base import Graph
from lfx.helpers import get_flow_inputs
from lfx.io import BoolInput, DropdownInput, Output, StrInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class FlowToolComponent(LCToolComponent):
    """将 Flow 封装为工具的组件。

    契约：`build_tool` 返回 `FlowTool` 实例；必要参数缺失会抛 `ValueError`。
    副作用：加载并解析 Flow 数据，更新 `self.status`。
    """
    display_name = "Flow as Tool"
    description = "Construct a Tool from a function that runs the loaded Flow."
    field_order = ["flow_name", "name", "description", "return_direct"]
    trace_type = "tool"
    name = "FlowTool"
    legacy: bool = True
    replacement = ["logic.RunFlow"]
    icon = "hammer"

    async def get_flow_names(self) -> list[str]:
        """获取当前用户可访问的 Flow 名称列表。"""
        flow_datas = await self.alist_flows()
        return [flow_data.data["name"] for flow_data in flow_datas]

    async def get_flow(self, flow_name: str) -> Data | None:
        """按名称获取 Flow 数据记录。"""
        flow_datas = await self.alist_flows()
        for flow_data in flow_datas:
            if flow_data.data["name"] == flow_name:
                return flow_data
        return None

    @override
    async def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据字段变化刷新构建配置选项。"""
        if field_name == "flow_name":
            build_config["flow_name"]["options"] = self.get_flow_names()

        return build_config

    inputs = [
        DropdownInput(
            name="flow_name", display_name="Flow Name", info="The name of the flow to run.", refresh_button=True
        ),
        StrInput(
            name="tool_name",
            display_name="Name",
            info="The name of the tool.",
        ),
        StrInput(
            name="tool_description",
            display_name="Description",
            info="The description of the tool; defaults to the Flow's description.",
        ),
        BoolInput(
            name="return_direct",
            display_name="Return Direct",
            info="Return the result directly from the Tool.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="api_build_tool", display_name="Tool", method="build_tool"),
    ]

    async def build_tool(self) -> Tool:
        """构建 FlowTool 实例并返回。

        关键路径（三步）：
        1) 校验 flow_name 并加载 Flow 数据
        2) 由 Flow 构建 Graph 并提取输入
        3) 组装 FlowTool 并更新状态描述
        异常流：Flow 缺失或构建失败会抛 `ValueError`。
        """
        FlowTool.model_rebuild()
        if "flow_name" not in self._attributes or not self._attributes["flow_name"]:
            msg = "Flow name is required"
            raise ValueError(msg)
        flow_name = self._attributes["flow_name"]
        flow_data = await self.get_flow(flow_name)
        if not flow_data:
            msg = "Flow not found."
            raise ValueError(msg)
        graph = Graph.from_payload(
            flow_data.data["data"],
            user_id=str(self.user_id),
        )
        try:
            graph.set_run_id(self.graph.run_id)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to set run_id", exc_info=True)
        inputs = get_flow_inputs(graph)
        tool_description = self.tool_description.strip() or flow_data.description
        tool = FlowTool(
            name=self.tool_name,
            description=tool_description,
            graph=graph,
            return_direct=self.return_direct,
            inputs=inputs,
            flow_id=str(flow_data.id),
            user_id=str(self.user_id),
            session_id=self.graph.session_id if hasattr(self, "graph") else None,
        )
        description_repr = repr(tool.description).strip("'")
        args_str = "\n".join([f"- {arg_name}: {arg_data['description']}" for arg_name, arg_data in tool.args.items()])
        self.status = f"{description_repr}\nArguments:\n{args_str}"
        return tool
