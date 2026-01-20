"""
模块名称：lfx.graph.edge.schema

本模块提供边相关的协议结构与数据模型，主要用于句柄数据的校验与类型对齐。主要功能包括：
- 功能1：定义 `EdgeData` 与句柄相关的 `TypedDict` 协议
- 功能2：提供 `TargetHandle`/`SourceHandle` 的 `Pydantic` 模型
- 功能3：提供 `Payload` 结果聚合容器

关键组件：
- `TargetHandle`：目标句柄模型（含别名与 loop 适配）
- `SourceHandle`：源句柄模型（含 `GroupNode` 名称修正）
- `Payload`：结果对集合容器

设计背景：统一前端字段命名与后端校验逻辑，减少边构建时的手动解析。
注意事项：字段别名依赖 `populate_by_name=True`；`from_loop_target_handle` 仅适配 loop 结构。
"""

from typing import Any

from pydantic import ConfigDict, Field, field_validator
from typing_extensions import TypedDict

from lfx.helpers.base_model import BaseModel


class SourceHandleDict(TypedDict, total=False):
    baseClasses: list[str]
    dataType: str
    id: str
    name: str | None
    output_types: list[str]


class TargetHandleDict(TypedDict):
    fieldName: str
    id: str
    inputTypes: list[str] | None
    type: str


class LoopTargetHandleDict(TypedDict):
    dataType: str
    id: str
    name: str
    output_types: list[str]


class EdgeDataDetails(TypedDict):
    sourceHandle: SourceHandleDict
    targetHandle: TargetHandleDict | LoopTargetHandleDict


class EdgeData(TypedDict, total=False):
    source: str
    target: str
    data: EdgeDataDetails


class ResultPair(BaseModel):
    """单次执行结果与附加信息的最小载体。

    契约：`result` 为主结果，`extra` 为可选附加信息；用于 `Payload` 聚合。
    关键路径：由调用方构造并追加到 `Payload.result_pairs`。
    决策：使用独立对象封装 `result`/`extra`；问题：需要保持顺序与可扩展性；
    方案：使用 `Pydantic` 模型；代价：序列化成本；重评：性能瓶颈明显时改为轻量 dict。
    """

    result: Any
    extra: Any


class Payload(BaseModel):
    """结果对集合容器，保持顺序并提供格式化输出。

    契约：`result_pairs` 为追加式列表；`format` 仅格式化除最后一项外的结果。
    关键路径：1) 追加结果对 2) 读取最后结果 3) 按需格式化历史结果。
    决策：保留完整历史而不做截断；问题：下游需要回溯上下文；
    方案：仅追加、不丢弃；代价：内存随结果增长；重评：引入最大保留条数后调整。
    """

    result_pairs: list[ResultPair] = []

    def __iter__(self):
        return iter(self.result_pairs)

    def add_result_pair(self, result: Any, extra: Any | None = None) -> None:
        self.result_pairs.append(ResultPair(result=result, extra=extra))

    def get_last_result_pair(self) -> ResultPair:
        return self.result_pairs[-1]

    # format all but the last result pair
    # into a string
    def format(self, sep: str = "\n") -> str:
        """格式化历史结果（不包含最后一项）。

        契约：返回字符串拼接结果；`sep` 用于行间分隔。
        异常流：无显式异常，依赖 `result_pairs` 内容可被 `str()`。
        排障入口：检查 `result_pairs` 是否包含 `None` 或不可序列化对象。
        """
        # Result: the result
        # Extra: the extra if it exists don't show if it doesn't
        return sep.join(
            [
                f"Result: {result_pair.result}\nExtra: {result_pair.extra}"
                if result_pair.extra is not None
                else f"Result: {result_pair.result}"
                for result_pair in self.result_pairs[:-1]
            ]
        )


class TargetHandle(BaseModel):
    """目标句柄模型，统一前端字段命名与运行时字段。

    契约：通过别名接收 `fieldName`/`inputTypes`；`input_types` 默认为空列表。
    关键路径：1) 通过别名解析字段 2) 可选转换 loop 句柄。
    决策：保留别名映射以兼容 camelCase；问题：前端字段无法直接映射 snake_case；
    方案：`alias` + `populate_by_name=True`；代价：序列化时需关注双命名；重评：前端迁移后移除别名。
    """

    model_config = ConfigDict(populate_by_name=True)
    field_name: str = Field(..., alias="fieldName", description="Field name for the target handle.")
    id: str = Field(..., description="Unique identifier for the target handle.")
    input_types: list[str] = Field(
        default_factory=list, alias="inputTypes", description="List of input types for the target handle."
    )
    type: str = Field(None, description="Type of the target handle.")

    @classmethod
    def from_loop_target_handle(cls, target_handle: LoopTargetHandleDict) -> "TargetHandle":
        """将 loop 句柄转换为标准目标句柄。

        契约：输入 `LoopTargetHandleDict`，输出 `TargetHandle`，不设置 `type`。
        异常流：缺失 `name`/`id` 时由 Pydantic 抛 `ValidationError`。
        排障入口：确认前端输出字段名是否为 `name` 与 `output_types`。
        """
        # The target handle is a loop edge
        # The target handle is a dict with the following keys:
        # - name: str
        # - id: str
        # - inputTypes: list[str]
        # - type: str
        # It is built from an Output, which is why it has a different structure
        return cls(
            field_name=target_handle.get("name"),
            id=target_handle.get("id"),
            input_types=target_handle.get("output_types"),
        )


class SourceHandle(BaseModel):
    """源句柄模型，提供 `GroupNode` 名称修正逻辑。

    契约：通过别名接收 `baseClasses`/`dataType`；`name` 在 GroupNode 场景被拆解。
    关键路径：1) 解析字段别名 2) 运行 `validate_name` 修正 GroupNode 名称。
    决策：在模型层处理 GroupNode 兼容；问题：前端 `name` 含前缀；
    方案：在校验器中拆分；代价：额外字符串处理；重评：前端输出规范化后移除该逻辑。
    """

    model_config = ConfigDict(populate_by_name=True)
    base_classes: list[str] = Field(
        default_factory=list, alias="baseClasses", description="List of base classes for the source handle."
    )
    data_type: str = Field(..., alias="dataType", description="Data type for the source handle.")
    id: str = Field(..., description="Unique identifier for the source handle.")
    name: str | None = Field(None, description="Name of the source handle.")
    output_types: list[str] = Field(default_factory=list, description="List of output types for the source handle.")

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v, info):
        if info.data["data_type"] == "GroupNode":
            # 'OpenAIModel-u4iGV_text_output'
            splits = v.split("_", 1)
            if len(splits) != 2:  # noqa: PLR2004
                msg = f"Invalid source handle name {v}"
                raise ValueError(msg)
            v = splits[1]
        return v
