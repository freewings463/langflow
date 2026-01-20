"""
模块名称：lfx.graph.edge.base

本模块提供图中边的运行时模型与循环边语义，主要用于解析前端句柄并校验连线合法性。主要功能包括：
- 功能1：解析 `EdgeData` 构建 `Edge` / `CycleEdge`
- 功能2：根据输入输出类型校验句柄兼容性
- 功能3：在循环边中写入目标参数以闭环执行

关键组件：
- `Edge`：普通边的解析与校验
- `CycleEdge`：循环边的结果兑现

设计背景：统一处理前端句柄协议与运行期类型匹配，避免执行期才暴露不兼容连接。
注意事项：校验失败会抛 `ValueError`；循环边依赖源节点已构建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from lfx.graph.edge.schema import EdgeData, LoopTargetHandleDict, SourceHandle, TargetHandle, TargetHandleDict
from lfx.log.logger import logger
from lfx.schema.schema import INPUT_FIELD_NAME

if TYPE_CHECKING:
    from lfx.graph.vertex.base import Vertex


class Edge:
    """连接源/目标节点的运行时边模型。

    契约：输入 `source`/`target` 与 `EdgeData`，初始化后提供 `target_param`、`matched_type` 与校验结果。
    关键路径：1) 解析句柄 2) 校验句柄 3) 记录匹配类型。
    决策：兼容旧/新句柄协议并存；问题：历史流程仍发送 `baseClasses`；
    方案：检测 `base_classes` 分支进入 `_legacy_*`；代价：双分支维护成本；
    重评：旧协议下线后移除 `_legacy_*` 分支。
    """

    def __init__(self, source: Vertex, target: Vertex, edge: EdgeData):
        """构建边并完成句柄/类型校验。

        关键路径（三步）：1) 解析 `edge` 句柄 2) 选择新/旧校验 3) 校验类型匹配。
        异常流：句柄缺失或类型不匹配时抛 `ValueError`。
        性能瓶颈：匹配复杂度与 `outputs`/`inputs` 数量线性相关。
        排障入口：日志关键字 `Edge data is empty`。
        """
        self.source_id: str = source.id if source else ""
        self.target_id: str = target.id if target else ""
        self.valid_handles: bool = False
        self.target_param: str | None = None
        self._target_handle: TargetHandleDict | str | None = None
        self._data = edge.copy()
        self.is_cycle = False
        if data := edge.get("data", {}):
            self._source_handle = data.get("sourceHandle", {})
            self._target_handle = cast("TargetHandleDict", data.get("targetHandle", {}))
            self.source_handle: SourceHandle = SourceHandle(**self._source_handle)
            if isinstance(self._target_handle, dict):
                try:
                    if "name" in self._target_handle:
                        self.target_handle: TargetHandle = TargetHandle.from_loop_target_handle(
                            cast("LoopTargetHandleDict", self._target_handle)
                        )
                    else:
                        self.target_handle = TargetHandle(**self._target_handle)
                except Exception as e:
                    if "inputTypes" in self._target_handle and self._target_handle["inputTypes"] is None:
                        # Check if self._target_handle['fieldName']
                        if hasattr(target, "custom_component"):
                            display_name = getattr(target.custom_component, "display_name", "")
                            msg = (
                                f"Component {display_name} field '{self._target_handle['fieldName']}' "
                                "might not be a valid input."
                            )
                            raise ValueError(msg) from e
                        msg = (
                            f"Field '{self._target_handle['fieldName']}' on {target.display_name} "
                            "might not be a valid input."
                        )
                        raise ValueError(msg) from e
                    raise

            else:
                msg = "Target handle is not a dictionary"
                raise ValueError(msg)
            self.target_param = self.target_handle.field_name
            # validate handles
            self.validate_handles(source, target)
        else:
            # Logging here because this is a breaking change
            logger.error("Edge data is empty")
            self._source_handle = edge.get("sourceHandle", "")  # type: ignore[assignment]
            self._target_handle = edge.get("targetHandle", "")  # type: ignore[assignment]
            # 'BaseLoader;BaseOutputParser|documents|PromptTemplate-zmTlD'
            # target_param is documents
            if isinstance(self._target_handle, str):
                self.target_param = self._target_handle.split("|")[1]
                self.source_handle = None  # type: ignore[assignment]
                self.target_handle = None  # type: ignore[assignment]
            else:
                msg = "Target handle is not a string"
                raise ValueError(msg)
        # Validate in __init__ to fail fast
        self.validate_edge(source, target)

    def to_data(self):
        return self._data

    def validate_handles(self, source, target) -> None:
        if isinstance(self._source_handle, str) or self.source_handle.base_classes:
            self._legacy_validate_handles(source, target)
        else:
            self._validate_handles(source, target)

    def _validate_handles(self, source, target) -> None:
        if self.target_handle.input_types is None:
            self.valid_handles = self.target_handle.type in self.source_handle.output_types
        elif self.target_handle.type is None:
            # ! This is not a good solution
            # This is a loop edge
            # If the target_handle.type is None, it means it's a loop edge
            # and we should check if the source_handle.output_types is not empty
            # and if the target_handle.input_types is empty or if any of the source_handle.output_types
            # is in the target_handle.input_types
            self.valid_handles = bool(self.source_handle.output_types) and (
                not self.target_handle.input_types
                or any(output_type in self.target_handle.input_types for output_type in self.source_handle.output_types)
            )

        elif self.source_handle.output_types is not None:
            self.valid_handles = (
                any(output_type in self.target_handle.input_types for output_type in self.source_handle.output_types)
                or self.target_handle.type in self.source_handle.output_types
            )

        if not self.valid_handles:
            logger.debug(self.source_handle)
            logger.debug(self.target_handle)
            msg = f"Edge between {source.display_name} and {target.display_name} has invalid handles"
            raise ValueError(msg)

    def _legacy_validate_handles(self, source, target) -> None:
        if self.target_handle.input_types is None:
            self.valid_handles = self.target_handle.type in self.source_handle.base_classes
        else:
            self.valid_handles = (
                any(baseClass in self.target_handle.input_types for baseClass in self.source_handle.base_classes)
                or self.target_handle.type in self.source_handle.base_classes
            )
        if not self.valid_handles:
            logger.debug(self.source_handle)
            logger.debug(self.target_handle)
            msg = f"Edge between {source.vertex_type} and {target.vertex_type} has invalid handles"
            raise ValueError(msg)

    def __setstate__(self, state):
        self.source_id = state["source_id"]
        self.target_id = state["target_id"]
        self.target_param = state["target_param"]
        self.source_handle = state.get("source_handle")
        self.target_handle = state.get("target_handle")
        self._source_handle = state.get("_source_handle")
        self._target_handle = state.get("_target_handle")
        self._data = state.get("_data")
        self.valid_handles = state.get("valid_handles")
        self.source_types = state.get("source_types")
        self.target_reqs = state.get("target_reqs")
        self.matched_type = state.get("matched_type")

    def validate_edge(self, source, target) -> None:
        # If the self.source_handle has base_classes, then we are using the legacy
        # way of defining the source and target handles
        if isinstance(self._source_handle, str) or self.source_handle.base_classes:
            self._legacy_validate_edge(source, target)
        else:
            self._validate_edge(source, target)

    def _validate_edge(self, source, target) -> None:
        """校验新协议下的类型匹配并标记结果。

        契约：读取 `source.outputs` 与 `target` 输入约束，设置 `self.valid`/`self.matched_type`。
        关键路径：1) 抽取 `source_handle` 输出 2) 区分 loop/常规输入 3) 记录首个匹配类型。
        决策：使用包含关系匹配类型名（`output_type in target_req`）；
        问题：历史类型名存在包含关系；方案：容错匹配；代价：潜在误匹配；重评：类型枚举化后改严格等值。
        异常流：无匹配类型时抛 `ValueError`；性能瓶颈：双层匹配 O(n*m)；
        排障入口：调试日志 `source_types`/`target_reqs`。
        """
        # Validate that the outputs of the source node are valid inputs
        # for the target node
        # .outputs is a list of Output objects as dictionaries
        # meaning: check for "types" key in each dictionary
        self.source_types = [output for output in source.outputs if output["name"] == self.source_handle.name]

        # Check if this is an loop input (loop target handle with output_types)
        is_loop_input = hasattr(self.target_handle, "input_types") and self.target_handle.input_types
        loop_input_types = []

        if is_loop_input:
            # For loop inputs, use the configured input_types
            # (which already includes original type + loop_types from frontend)
            loop_input_types = list(self.target_handle.input_types)
            self.valid = any(
                any(output_type in loop_input_types for output_type in output["types"]) for output in self.source_types
            )
            # Find the first matching type
            self.matched_type = next(
                (
                    output_type
                    for output in self.source_types
                    for output_type in output["types"]
                    if output_type in loop_input_types
                ),
                None,
            )
        else:
            # Standard validation for regular inputs
            self.target_reqs = target.required_inputs + target.optional_inputs
            # Both lists contain strings and sometimes a string contains the value we are
            # looking for e.g. comgin_out=["Chain"] and target_reqs=["LLMChain"]
            # so we need to check if any of the strings in source_types is in target_reqs
            self.valid = any(
                any(output_type in target_req for output_type in output["types"])
                for output in self.source_types
                for target_req in self.target_reqs
            )
            # Update the matched type to be the first found match
            self.matched_type = next(
                (
                    output_type
                    for output in self.source_types
                    for output_type in output["types"]
                    for target_req in self.target_reqs
                    if output_type in target_req
                ),
                None,
            )

        no_matched_type = self.matched_type is None
        if no_matched_type:
            logger.debug(self.source_types)
            logger.debug(self.target_reqs if not is_loop_input else loop_input_types)
            msg = f"Edge between {source.vertex_type} and {target.vertex_type} has no matched type."
            raise ValueError(msg)

    def _legacy_validate_edge(self, source, target) -> None:
        """校验旧协议下的类型匹配。

        契约：使用 `source.output` 与 `target` 输入约束，设置 `self.valid`/`self.matched_type`。
        关键路径：1) 汇总输出类型 2) 按包含关系匹配 3) 记录首个匹配类型。
        异常流：无匹配类型时抛 `ValueError`。
        性能瓶颈：匹配复杂度与类型数量线性相关。
        排障入口：调试日志 `source_types`/`target_reqs`。
        """
        # Validate that the outputs of the source node are valid inputs
        # for the target node
        self.source_types = source.output
        self.target_reqs = target.required_inputs + target.optional_inputs
        # Both lists contain strings and sometimes a string contains the value we are
        # looking for e.g. comgin_out=["Chain"] and target_reqs=["LLMChain"]
        # so we need to check if any of the strings in source_types is in target_reqs
        self.valid = any(output in target_req for output in self.source_types for target_req in self.target_reqs)
        # Get what type of input the target node is expecting

        self.matched_type = next(
            (output for output in self.source_types if output in self.target_reqs),
            None,
        )
        no_matched_type = self.matched_type is None
        if no_matched_type:
            logger.debug(self.source_types)
            logger.debug(self.target_reqs)
            msg = f"Edge between {source.vertex_type} and {target.vertex_type} has no matched type"
            raise ValueError(msg)

    def __repr__(self) -> str:
        if (hasattr(self, "source_handle") and self.source_handle) and (
            hasattr(self, "target_handle") and self.target_handle
        ):
            return f"{self.source_id} -[{self.source_handle.name}->{self.target_handle.field_name}]-> {self.target_id}"
        return f"{self.source_id} -[{self.target_param}]-> {self.target_id}"

    def __hash__(self) -> int:
        return hash(self.__repr__())

    def __eq__(self, /, other: object) -> bool:
        if not isinstance(other, Edge):
            return False
        return (
            self._source_handle == other._source_handle
            and self._target_handle == other._target_handle
            and self.target_param == other.target_param
        )

    def __str__(self) -> str:
        return self.__repr__()


class CycleEdge(Edge):
    """循环边：在执行期将源节点结果写回目标节点参数。

    契约：依赖 `matched_type` 判定写回的数据来源；通过 `honor` 兑现后可重复读取结果。
    关键路径：1) 读取源节点结果 2) 写入目标参数 3) 标记已兑现。
    决策：循环边不触发构建未完成的节点；问题：避免在只读阶段隐式构建；
    方案：未构建即抛错；代价：调用方需确保构建顺序；重评：引入显式构建阶段后评估。
    """

    def __init__(self, source: Vertex, target: Vertex, raw_edge: EdgeData):
        super().__init__(source, target, raw_edge)
        self.is_fulfilled = False  # Whether the contract has been fulfilled.
        self.result: Any = None
        self.is_cycle = True
        source.has_cycle_edges = True
        target.has_cycle_edges = True

    async def honor(self, source: Vertex, target: Vertex) -> None:
        """兑现循环边契约并写入目标参数。

        关键路径（三步）：1) 校验源节点已构建 2) 选取 `built_result`/`built_object` 3) 写入目标参数。
        异常流：源节点未构建时抛 `ValueError`。
        性能瓶颈：无显著瓶颈，主要为内存赋值。
        排障入口：异常信息 `Source vertex ... is not built.`。
        """
        if self.is_fulfilled:
            return

        if not source.built:
            # The system should be read-only, so we should not be building vertices
            # that are not already built.
            msg = f"Source vertex {source.id} is not built."
            raise ValueError(msg)

        if self.matched_type == "Text":
            self.result = source.built_result
        else:
            self.result = source.built_object

        target.params[self.target_param] = self.result
        self.is_fulfilled = True

    async def get_result_from_source(self, source: Vertex, target: Vertex):
        """返回循环边结果，必要时先兑现。

        契约：若未兑现则先执行 `honor`；始终返回 `self.result`。
        异常流：沿用 `honor` 的 `ValueError`。
        排障入口：关注 `ChatOutput` 参数 `message` 的空值判定逻辑。
        """
        # Fulfill the contract if it has not been fulfilled.
        if not self.is_fulfilled:
            await self.honor(source, target)

        # If the target vertex is a power component we log messages
        if (
            target.vertex_type == "ChatOutput"
            and isinstance(target.params.get(INPUT_FIELD_NAME), str | dict)
            and target.params.get("message") == ""
        ):
            return self.result
        return self.result

    def __repr__(self) -> str:
        str_repr = super().__repr__()
        # Add a symbol to show this is a cycle edge
        return f"{str_repr} 🔄"
