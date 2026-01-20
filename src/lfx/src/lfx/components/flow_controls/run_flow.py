"""
模块名称：Run Flow 组件

本模块提供在当前项目内执行其他 Flow 的组件封装，主要用于流程复用与
作为代理工具调用。
主要功能包括：
- 动态加载 Flow 图并同步构建配置
- 支持 Flow 选择与过期检测更新

关键组件：
- `RunFlowComponent`：Flow 执行组件

设计背景：复用已构建的 Flow 并作为可组合的运行单元。
注意事项：Flow 过期检测依赖更新时间字段。
"""

from datetime import datetime
from typing import Any

from lfx.base.tools.run_flow import RunFlowBaseComponent
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class RunFlowComponent(RunFlowBaseComponent):
    """运行其他 Flow 的组件。

    契约：输入为 Flow 选择与参数，输出为 Flow 执行结果。
    副作用：可能加载 Graph 并更新 build_config。
    失败语义：Flow 加载/构建异常会抛 `RuntimeError`。
    """
    display_name = "Run Flow"
    description = (
        "Executes another flow from within the same project. Can also be used as a tool for agents."
        " \n **Select a Flow to use the tool mode**"
    )
    documentation: str = "https://docs.langflow.org/run-flow"
    beta = True
    name = "RunFlow"
    icon = "Workflow"

    inputs = RunFlowBaseComponent.get_base_inputs()
    outputs = RunFlowBaseComponent.get_base_outputs()

    async def update_build_config(
        self,
        build_config: dotdict,
        field_value: Any,
        field_name: str | None = None,
    ):
        """根据字段变化更新构建配置与 Flow 选项。

        关键路径（三步）：
        1) 补齐缺失字段并准备默认结构
        2) 刷新 Flow 列表或根据选择加载 Graph
        3) 写回更新后的 build_config
        异常流：Flow 加载失败会抛 `RuntimeError`。
        """
        missing_keys = [key for key in self.default_keys if key not in build_config]
        for key in missing_keys:
            if key == "flow_name_selected":
                build_config[key] = {"options": [], "options_metadata": [], "value": None}
            elif key == "flow_id_selected":
                build_config[key] = {"value": None}
            elif key == "cache_flow":
                build_config[key] = {"value": False}
            else:
                build_config[key] = {}
        if field_name == "flow_name_selected" and (build_config.get("is_refresh", False) or field_value is None):
            # 实现：刷新按钮触发或初始化时加载 Flow 列表
            options: list[str] = await self.alist_flows_by_flow_folder()
            build_config["flow_name_selected"]["options"] = [flow.data["name"] for flow in options]
            build_config["flow_name_selected"]["options_metadata"] = []
            for flow in options:
                # 实现：填充 options_metadata
                build_config["flow_name_selected"]["options_metadata"].append(
                    {"id": flow.data["id"], "updated_at": flow.data["updated_at"]}
                )
                # 注意：选中 Flow 过期时自动更新
                if str(flow.data["id"]) == self.flow_id_selected:
                    await self.check_and_update_stale_flow(flow, build_config)
        elif field_name in {"flow_name_selected", "flow_id_selected"} and field_value is not None:
            # 实现：选择 Flow 后加载并更新配置
            try:
                # 实现：当字段为名称时派生 flow_id
                build_config["flow_id_selected"]["value"] = (
                    self.get_selected_flow_meta(build_config, "id") or build_config["flow_id_selected"]["value"]
                )
                updated_at = self.get_selected_flow_meta(build_config, "updated_at")
                await self.load_graph_and_update_cfg(
                    build_config, build_config["flow_id_selected"]["value"], updated_at
                )
            except Exception as e:
                msg = f"Error building graph for flow {field_value}"
                await logger.aexception(msg)
                raise RuntimeError(msg) from e

        return build_config

    def get_selected_flow_meta(self, build_config: dotdict, field: str) -> dict:
        """从 build_config 中获取已选 Flow 的元数据。"""
        return build_config.get("flow_name_selected", {}).get("selected_metadata", {}).get(field)

    async def load_graph_and_update_cfg(
        self,
        build_config: dotdict,
        flow_id: str,
        updated_at: str | datetime,
    ) -> None:
        """加载 Flow 图并更新构建配置。"""
        graph = await self.get_graph(
            flow_id_selected=flow_id,
            updated_at=self.get_str_isots(updated_at),
        )
        self.update_build_config_from_graph(build_config, graph)

    def should_update_stale_flow(self, flow: Data, build_config: dotdict) -> bool:
        """判断选中 Flow 是否过期需要更新。"""
        return (
            (updated_at := self.get_str_isots(flow.data["updated_at"]))  # 注意：数据库最新时间
            and (stale_at := self.get_selected_flow_meta(build_config, "updated_at"))  # 注意：配置中旧时间
            and self._parse_timestamp(updated_at) > self._parse_timestamp(stale_at)  # 注意：过期判断
        )

    async def check_and_update_stale_flow(self, flow: Data, build_config: dotdict) -> None:
        """检测 Flow 是否过期并按需更新。"""
        # 注意：TODO 改进契约/返回值
        if self.should_update_stale_flow(flow, build_config):
            await self.load_graph_and_update_cfg(
                build_config,
                flow.data["id"],
                flow.data["updated_at"],
            )

    def get_str_isots(self, date: datetime | str) -> str:
        """将 datetime 或字符串转换为 ISO 字符串。"""
        return date.isoformat() if hasattr(date, "isoformat") else date
