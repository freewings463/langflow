"""
模块名称：流程工具

本模块将 `Flow` 运行封装为 `LangChain` 工具，支持同步/异步执行与输入校验。
主要功能包括：
- 根据 `Flow` 输入生成工具 `schema`
- 运行 `Flow` 并格式化输出
- 同步与异步执行路径

关键组件：`FlowTool`
设计背景：将 `Flow` 作为工具暴露给 `agent`，统一调用与输出格式
注意事项：参数数量需与输入一致；未找到 `schema` 时抛 `ToolException`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, ToolException
from typing_extensions import override

from lfx.base.flow_processing.utils import build_data_from_result_data, format_flow_output_data
from lfx.helpers import build_schema_from_inputs, get_arg_names, get_flow_inputs, run_flow
from lfx.log.logger import logger
from lfx.utils.async_helpers import run_until_complete

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from pydantic.v1 import BaseModel

    from lfx.graph.graph.base import Graph
    from lfx.graph.vertex.base import Vertex


class FlowTool(BaseTool):
    """`Flow` 工具封装。
    契约：输入为 `Flow` 所需参数；输出为格式化后的字符串。
    关键路径：构建输入 `schema` → 执行 `Flow` → 格式化输出。
    决策：输出统一为字符串。问题：`agent` 需要稳定输出格式；方案：格式化文本；代价：结构化信息丢失；重评：当下游支持结构化输出时。
    """

    name: str
    description: str
    graph: Graph | None = None
    flow_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    inputs: list[Vertex] = []
    get_final_results_only: bool = True

    @property
    def args(self) -> dict:
        """返回工具参数的 `schema` 属性字典。
        契约：返回 `schema` 中的 properties。
        关键路径：获取输入 `schema` → 取 properties。
        决策：直接暴露 `schema.properties`。问题：保持与 `LangChain` 约定一致；方案：透传 `schema`；代价：依赖 `schema` 格式；重评：当 `schema` 结构变更时。
        """
        schema = self.get_input_schema()
        return schema.schema()["properties"]

    @override
    def get_input_schema(  # type: ignore[misc]
        self, config: RunnableConfig | None = None
    ) -> type[BaseModel]:
        """返回工具输入 `schema`。
        契约：优先返回 `args_schema`；否则根据 `graph` 构建；缺失时抛 `ToolException`。
        关键路径：优先使用 `args_schema` → 否则由 `graph` 构建 → 失败抛错。
        决策：无 `schema` 直接报错。问题：工具调用无法校验；方案：显式失败；代价：工具不可用；重评：当允许运行期推断时。
        """
        if self.args_schema is not None:
            return self.args_schema
        if self.graph is not None:
            return build_schema_from_inputs(self.name, get_flow_inputs(self.graph))
        msg = "No input schema available."
        raise ToolException(msg)

    def _run(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """同步执行 `Flow` 工具。
        契约：返回格式化后的输出字符串。
        关键路径：参数校验 → 组装 `tweaks` → 执行 `flow` → 格式化输出。
        决策：位置参数必须与输入数量一致。问题：避免错位映射；方案：数量校验；代价：调用约束更严格；重评：当支持默认参数时。
        """
        args_names = get_arg_names(self.inputs)
        if len(args_names) == len(args):
            kwargs = {arg["arg_name"]: arg_value for arg, arg_value in zip(args_names, args, strict=True)}
        elif len(args_names) != len(args) and len(args) != 0:
            msg = "Number of arguments does not match the number of inputs. Pass keyword arguments instead."
            raise ToolException(msg)
        tweaks = {arg["component_name"]: kwargs[arg["arg_name"]] for arg in args_names}

        run_outputs = run_until_complete(
            run_flow(
                graph=self.graph,
                tweaks={key: {"input_value": value} for key, value in tweaks.items()},
                flow_id=self.flow_id,
                user_id=self.user_id,
                session_id=self.session_id,
            )
        )
        if not run_outputs:
            return "No output"
        run_output = run_outputs[0]

        data = []
        if run_output is not None:
            for output in run_output.outputs:
                if output:
                    data.extend(build_data_from_result_data(output))
        return format_flow_output_data(data)

    def validate_inputs(self, args_names: list[dict[str, str]], args: Any, kwargs: Any):
        """校验并规范化输入参数。
        契约：返回合并后的 kwargs；缺失参数抛 ToolException。
        关键路径：校验数量 → 合并 kwargs → 校验缺失参数。
        决策：缺失参数直接失败。问题：避免运行期隐式错误；方案：前置校验；代价：不允许部分输入；重评：当支持可选输入时。
        """
        if len(args) > 0 and len(args) != len(args_names):
            msg = "Number of positional arguments does not match the number of inputs. Pass keyword arguments instead."
            raise ToolException(msg)

        if len(args) == len(args_names):
            kwargs = {arg_name["arg_name"]: arg_value for arg_name, arg_value in zip(args_names, args, strict=True)}

        missing_args = [arg["arg_name"] for arg in args_names if arg["arg_name"] not in kwargs]
        if missing_args:
            msg = f"Missing required arguments: {', '.join(missing_args)}"
            raise ToolException(msg)

        return kwargs

    def build_tweaks_dict(self, args, kwargs):
        """构建 `tweaks` 字典。
        契约：返回 `component_name` → `value` 的映射。
        关键路径：校验输入 → 组装映射。
        决策：基于输入顺序映射参数。问题：保持与 `Flow` 输入顺序一致；方案：按 `args_names` 组装；代价：依赖顺序一致性；重评：当输入改为显式映射时。
        """
        args_names = get_arg_names(self.inputs)
        kwargs = self.validate_inputs(args_names=args_names, args=args, kwargs=kwargs)
        return {arg["component_name"]: kwargs[arg["arg_name"]] for arg in args_names}

    async def _arun(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """异步执行 `Flow` 工具。
        契约：返回格式化后的输出字符串。
        关键路径：构建 `tweaks` → 执行 `run_flow` → 格式化输出。
        决策：`run_id` 读取失败时忽略。问题：不影响运行但可追踪性下降；方案：异常吞并并记录日志；代价：缺失 `run_id`；重评：当必须强制 `run_id` 时。
        """
        tweaks = self.build_tweaks_dict(args, kwargs)
        try:
            run_id = self.graph.run_id if hasattr(self, "graph") and self.graph else None
        except Exception:  # noqa: BLE001
            logger.warning("Failed to set run_id", exc_info=True)
            run_id = None
        run_outputs = await run_flow(
            tweaks={key: {"input_value": value} for key, value in tweaks.items()},
            flow_id=self.flow_id,
            user_id=self.user_id,
            run_id=run_id,
            session_id=self.session_id,
            graph=self.graph,
        )
        if not run_outputs:
            return "No output"
        run_output = run_outputs[0]

        data = []
        if run_output is not None:
            for output in run_output.outputs:
                if output:
                    data.extend(build_data_from_result_data(output))
        return format_flow_output_data(data)
