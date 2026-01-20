"""
模块名称：LangChain 工具组件基类

本模块定义与 LangChain 工具对接的组件基类，主要用于统一组件对外输出与工具构建的接口契约。主要功能包括：
- 约束组件必须提供 `run_model` 与 `build_tool` 两个输出端
- 规范输出类型（`Data`/`DataFrame`/`Tool`）以便上层编排

关键组件：
- `LCToolComponent`：LangChain 工具组件基类

设计背景：Langflow 组件与 LangChain 工具需共享一致的输出契约，避免运行期才发现方法/输出缺失。
注意事项：本模块不实现具体业务逻辑，仅定义抽象接口与校验规则。
"""

from abc import abstractmethod
from collections.abc import Sequence

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Tool
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class LCToolComponent(Component):
    """LangChain 工具组件基类。

    契约：子类必须实现 `run_model` 与 `build_tool`，且输出名称需与 `outputs` 中配置一致。
    失败语义：缺少输出或方法时 `_validate_outputs` 抛 `ValueError`，调用方需修正组件定义。
    副作用：无（仅校验与接口约束）。
    """

    trace_type = "tool"
    outputs = [
        Output(name="api_run_model", display_name="Data", method="run_model"),
        Output(name="api_build_tool", display_name="Tool", method="build_tool"),
    ]

    def _validate_outputs(self) -> None:
        """校验组件输出配置与必需方法是否齐全。

        契约：`outputs` 中必须包含 `run_model` 与 `build_tool` 的输出定义，且类中存在同名方法。
        失败语义：缺失时抛 `ValueError`，用于在加载阶段阻断不完整组件。
        副作用：无。
        """

        required_output_methods = ["run_model", "build_tool"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    @abstractmethod
    def run_model(self) -> Data | list[Data] | DataFrame:
        """运行模型并返回结构化输出。

        契约：返回 `Data`/`DataFrame` 或其列表，用于上层统一消费。
        失败语义：由具体实现定义，推荐抛可解释异常并由上层捕获处理。
        副作用：由具体实现决定（可能包含外部调用）。
        """

    @abstractmethod
    def build_tool(self) -> Tool | Sequence[Tool]:
        """构建 LangChain 工具实例。

        契约：返回单个或多个 `Tool`，用于注册到工具链。
        失败语义：由具体实现定义；构建失败应抛异常而非返回空值。
        副作用：由具体实现决定（可能包含网络/加载模型等）。
        """
