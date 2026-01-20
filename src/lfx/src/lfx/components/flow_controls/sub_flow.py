"""
模块名称：Sub Flow 组件

本模块提供将 Flow 转为组件并动态生成输入字段的能力，主要用于在当前
流程内嵌调用其他 Flow。
主要功能包括：
- 获取 Flow 列表并加载 Graph
- 从 Graph 输入顶点生成动态输入字段
- 执行子 Flow 并整理输出为 Data 列表

关键组件：
- `SubFlowComponent`：子流程组件

设计背景：在不显式工具化的场景下复用 Flow 逻辑。
注意事项：组件已标记为 legacy，推荐使用替代组件。
"""

from typing import Any

from lfx.base.flow_processing.utils import build_data_from_result_data
from lfx.custom.custom_component.component import Component
from lfx.graph.graph.base import Graph
from lfx.graph.vertex.base import Vertex
from lfx.helpers import get_flow_inputs
from lfx.io import DropdownInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class SubFlowComponent(Component):
    """将 Flow 包装为动态输入组件的子流程组件。"""
    display_name = "Sub Flow"
    description = "Generates a Component from a Flow, with all of its inputs, and "
    name = "SubFlow"
    legacy: bool = True
    replacement = ["logic.RunFlow"]
    icon = "Workflow"

    async def get_flow_names(self) -> list[str]:
        """获取可用 Flow 名称列表。"""
        flow_data = await self.alist_flows()
        return [flow_data.data["name"] for flow_data in flow_data]

    async def get_flow(self, flow_name: str) -> Data | None:
        """按名称获取 Flow 数据记录。"""
        flow_datas = await self.alist_flows()
        for flow_data in flow_datas:
            if flow_data.data["name"] == flow_name:
                return flow_data
        return None

    async def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据 Flow 选择动态更新构建配置。

        关键路径（三步）：
        1) 刷新 Flow 名称选项
        2) 选择 Flow 后加载 Graph
        3) 生成输入字段并写回配置
        异常流：Flow 加载或解析失败会记录日志。
        """
        if field_name == "flow_name":
            build_config["flow_name"]["options"] = await self.get_flow_names()

        for key in list(build_config.keys()):
            if key not in [x.name for x in self.inputs] + ["code", "_type", "get_final_results_only"]:
                del build_config[key]
        if field_value is not None and field_name == "flow_name":
            try:
                flow_data = await self.get_flow(field_value)
            except Exception:  # noqa: BLE001
                await logger.aexception(f"Error getting flow {field_value}")
            else:
                if not flow_data:
                    msg = f"Flow {field_value} not found."
                    await logger.aerror(msg)
                else:
                    try:
                        graph = Graph.from_payload(flow_data.data["data"])
                        # 实现：获取图的输入顶点
                        inputs = get_flow_inputs(graph)
                        # 实现：将输入字段写入构建配置
                        build_config = self.add_inputs_to_build_config(inputs, build_config)
                    except Exception:  # noqa: BLE001
                        await logger.aexception(f"Error building graph for flow {field_value}")

        return build_config

    def add_inputs_to_build_config(self, inputs_vertex: list[Vertex], build_config: dotdict):
        """将 Flow 输入顶点转换为 build_config 字段。

        契约：按 `vertex.id|input_name` 生成唯一字段名。
        副作用：修改传入的 build_config。
        """
        new_fields: list[dotdict] = []

        for vertex in inputs_vertex:
            new_vertex_inputs = []
            field_template = vertex.data["node"]["template"]
            for inp in field_template:
                if inp not in {"code", "_type"}:
                    field_template[inp]["display_name"] = (
                        vertex.display_name + " - " + field_template[inp]["display_name"]
                    )
                    field_template[inp]["name"] = vertex.id + "|" + inp
                    new_vertex_inputs.append(field_template[inp])
            new_fields += new_vertex_inputs
        for field in new_fields:
            build_config[field["name"]] = field
        return build_config

    inputs = [
        DropdownInput(
            name="flow_name",
            display_name="Flow Name",
            info="The name of the flow to run.",
            options=[],
            refresh_button=True,
            real_time_refresh=True,
        ),
    ]

    outputs = [Output(name="flow_outputs", display_name="Flow Outputs", method="generate_results")]

    async def generate_results(self) -> list[Data]:
        """执行子 Flow 并返回所有输出 Data。

        关键路径（三步）：
        1) 将动态字段聚合为 tweaks
        2) 运行子 Flow 获取输出
        3) 将输出转换为 Data 列表
        异常流：无显式异常抛出，空输出返回空列表。
        """
        tweaks: dict = {}
        for field in self._attributes:
            if field != "flow_name" and "|" in field:
                [node, name] = field.split("|")
                if node not in tweaks:
                    tweaks[node] = {}
                tweaks[node][name] = self._attributes[field]
        flow_name = self._attributes.get("flow_name")
        run_outputs = await self.run_flow(
            tweaks=tweaks,
            flow_name=flow_name,
            output_type="all",
        )
        data: list[Data] = []
        if not run_outputs:
            return data
        run_output = run_outputs[0]

        if run_output is not None:
            for output in run_output.outputs:
                if output:
                    data.extend(build_data_from_result_data(output))
        return data
