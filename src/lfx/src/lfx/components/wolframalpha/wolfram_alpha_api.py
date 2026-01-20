"""
模块名称：WolframAlpha API 组件

模块目的：封装 WolframAlpha 查询能力并输出结构化结果。
使用场景：在流程中提交自然语言/计算类问题，获取可读答案。
主要功能包括：
- 定义查询输入与鉴权参数（`app_id`）
- 调用 `WolframAlphaAPIWrapper` 获取答案
- 将结果转换为 `Data`/`DataFrame` 输出

关键组件：
- `WolframAlphaAPIComponent`：工具组件入口

设计背景：复用 LangChain 社区封装以减少 API 适配工作。
注意：`app_id` 缺失或无效会导致调用失败，调用方需提示配置问题。
"""

from langchain_community.utilities.wolfram_alpha import WolframAlphaAPIWrapper

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import MultilineInput, SecretStrInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class WolframAlphaAPIComponent(LCToolComponent):
    """WolframAlpha API 工具组件。

    契约：输入查询文本与 `app_id`，输出 `DataFrame`。
    关键路径：`fetch_content` 调用 wrapper 并封装结果，`fetch_content_dataframe` 统一输出。

    决策：通过 `WolframAlphaAPIWrapper` 适配 WolframAlpha
    问题：需要统一工具组件接口并复用上游封装
    方案：使用 LangChain 社区 wrapper
    代价：受上游接口与返回格式稳定性影响
    重评：当需要更细粒度字段或调用参数时
    """
    display_name = "WolframAlpha API"
    description = """Enables queries to WolframAlpha for computational data, facts, and calculations across various \
topics, delivering structured responses."""
    name = "WolframAlphaAPI"

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    inputs = [
        MultilineInput(
            name="input_value", display_name="Input Query", info="Example query: 'What is the population of France?'"
        ),
        SecretStrInput(name="app_id", display_name="WolframAlpha App ID", required=True),
    ]

    icon = "WolframAlphaAPI"

    def run_model(self) -> DataFrame:
        """组件运行入口，保持与框架 `run_model` 约定一致。"""
        return self.fetch_content_dataframe()

    def build_tool(self) -> Tool:
        """构建 Langflow 可调用的 Tool。

        契约：返回工具实例，`func` 绑定为 wrapper 的 `run`。
        副作用：创建 wrapper（不立即触发外部请求）。
        """
        wrapper = self._build_wrapper()
        return Tool(name="wolfram_alpha_api", description="Answers mathematical questions.", func=wrapper.run)

    def _build_wrapper(self) -> WolframAlphaAPIWrapper:
        """构建 WolframAlpha API wrapper。

        失败语义：`app_id` 无效将在调用阶段触发异常。
        """
        return WolframAlphaAPIWrapper(wolfram_alpha_appid=self.app_id)

    def fetch_content(self) -> list[Data]:
        """执行查询并返回结构化结果列表。

        契约：返回仅包含单条 `Data` 的列表。
        副作用：调用外部 WolframAlpha 服务（网络 I/O）。

        关键路径（三步）：
        1) 构建 wrapper
        2) 执行查询并获取文本结果
        3) 封装为 `Data` 并更新 `status`

        注意：上游错误会以异常形式向外传播。
        性能：远端计算耗时随问题复杂度上升。
        排障：关注上游异常堆栈与返回错误信息。
        """
        wrapper = self._build_wrapper()
        result_str = wrapper.run(self.input_value)
        data = [Data(text=result_str)]
        self.status = data
        return data

    def fetch_content_dataframe(self) -> DataFrame:
        """将查询结果转换为 `DataFrame` 以便下游消费。

        契约：返回包含查询结果的 `DataFrame`。
        失败语义：若上游失败将由 `fetch_content` 抛异常。
        """
        data = self.fetch_content()
        return DataFrame(data)
