"""
模块名称：子流程组件（已停用）

本模块提供动态加载并执行子流程的能力，主要用于在旧流程中复用已有 flow。主要功能包括：
- 异步获取 flow 列表并更新组件配置
- 根据选择的 flow 动态生成输入字段
- 运行子流程并返回标准化 `Data`

关键组件：
- `SubFlowComponent`：子流程组件

设计背景：在单一流程中复用其他已保存 flow 的能力。
注意事项：依赖 flow 存储与 `run_flow` 接口，配置更新具有副作用。
"""

from typing import TYPE_CHECKING, Any

from lfx.base.flow_processing.utils import build_data_from_result_data
from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.graph.graph.base import Graph
from lfx.graph.vertex.base import Vertex
from lfx.helpers import get_flow_inputs
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.template.field.base import Input

if TYPE_CHECKING:
    from lfx.graph.schema import RunOutputs


class SubFlowComponent(CustomComponent):
    """子流程组件。

    契约：根据 `flow_name` 运行子流程并返回 `Data` 列表。
    失败语义：flow 不存在时记录错误并返回空结果。
    副作用：动态修改组件配置、调用 `run_flow`、写入日志。
    """
    display_name = "Sub Flow"
    description = (
        "Dynamically Generates a Component from a Flow. The output is a list of data with keys 'result' and 'message'."
    )
    beta: bool = True
    field_order = ["flow_name"]
    name = "SubFlow"

    async def get_flow_names(self) -> list[str]:
        """获取可用的 flow 名称列表。

        契约：返回 `alist_flows` 中的 `name` 字段列表。
        失败语义：上游接口异常时抛出。
        副作用：无。
        """
        flow_datas = await self.alist_flows()
        return [flow_data.data["name"] for flow_data in flow_datas]

    async def get_flow(self, flow_name: str) -> Data | None:
        """根据名称查找 flow 数据。

        契约：名称匹配时返回 `Data`，否则返回 None。
        失败语义：上游接口异常时抛出。
        副作用：无。
        """
        flow_datas = await self.alist_flows()
        for flow_data in flow_datas:
            if flow_data.data["name"] == flow_name:
                return flow_data
        return None

    async def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """动态更新组件配置。

        契约：当 `flow_name` 变化时刷新可选项并注入子流程输入字段。
        失败语义：获取 flow 失败时记录日志，不抛出。
        副作用：修改 `build_config` 结构。

        关键路径（三步）：
        1) 刷新 `flow_name` 可选项
        2) 清理无关字段保持配置最小化
        3) 若选择了 flow，则解析其输入并注入配置
        """
        await logger.adebug(f"Updating build config with field value {field_value} and field name {field_name}")
        if field_name == "flow_name":
            build_config["flow_name"]["options"] = await self.get_flow_names()
        # 注意：清理无关字段，避免旧字段残留
        for key in list(build_config.keys()):
            if key not in {*self.field_order, "code", "_type", "get_final_results_only"}:
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
                        inputs = get_flow_inputs(graph)
                        build_config = self.add_inputs_to_build_config(inputs, build_config)
                    except Exception:  # noqa: BLE001
                        await logger.aexception(f"Error building graph for flow {field_value}")

        return build_config

    def add_inputs_to_build_config(self, inputs: list[Vertex], build_config: dotdict):
        """将子流程输入映射为组件字段。

        契约：为每个输入节点生成 `Input` 字段并写入配置。
        失败语义：无显式错误处理。
        副作用：修改 `build_config`。
        """
        new_fields: list[Input] = []
        for vertex in inputs:
            field = Input(
                display_name=vertex.display_name,
                name=vertex.id,
                info=vertex.description,
                field_type="str",
                value=None,
            )
            new_fields.append(field)
        logger.debug(new_fields)
        for field in new_fields:
            build_config[field.name] = field.to_dict()
        return build_config

    def build_config(self):
        """返回基础配置模板。

        契约：包含 `flow_name` 与可选 `tweaks` 配置。
        失败语义：无。
        副作用：无。
        """
        return {
            "input_value": {
                "display_name": "Input Value",
                "multiline": True,
            },
            "flow_name": {
                "display_name": "Flow Name",
                "info": "The name of the flow to run.",
                "options": [],
                "real_time_refresh": True,
                "refresh_button": True,
            },
            "tweaks": {
                "display_name": "Tweaks",
                "info": "Tweaks to apply to the flow.",
            },
            "get_final_results_only": {
                "display_name": "Get Final Results Only",
                "info": "If False, the output will contain all outputs from the flow.",
                "advanced": True,
            },
        }

    async def build(self, flow_name: str, **kwargs) -> list[Data]:
        """运行子流程并返回 `Data` 列表。

        契约：根据 `flow_name` 执行子流程并解析输出。
        失败语义：运行失败时返回空列表或由 `run_flow` 抛异常。
        副作用：调用 `run_flow` 并更新组件 `status`。

        关键路径（三步）：
        1) 将输入参数转换为 `tweaks`
        2) 运行子流程并获取输出
        3) 解析输出为 `Data` 列表
        """
        tweaks = {key: {"input_value": value} for key, value in kwargs.items()}
        run_outputs: list[RunOutputs | None] = await self.run_flow(
            tweaks=tweaks,
            flow_name=flow_name,
        )
        if not run_outputs:
            return []
        run_output = run_outputs[0]

        data = []
        if run_output is not None:
            for output in run_output.outputs:
                if output:
                    data.extend(build_data_from_result_data(output))

        self.status = data
        await logger.adebug(data)
        return data
