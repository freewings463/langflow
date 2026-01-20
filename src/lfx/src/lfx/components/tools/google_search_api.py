"""
模块名称：`Google Search API` 工具组件

本模块封装 Google Search API 的查询能力，并将结果转换为 `Data` 输出。
主要功能包括：
- 构建 API 包装器并执行检索
- 将结果列表映射为结构化数据
- 提供 LangChain 工具接口

关键组件：
- `GoogleSearchAPIComponent.run_model`：执行搜索并输出结果
- `GoogleSearchAPIComponent._build_wrapper`：初始化 API 包装器

设计背景：为低代码流程提供可配置的搜索能力（已标记为弃用）。
注意事项：依赖 `langchain-google-community`，缺失时会抛异常。
"""

from langchain_core.tools import Tool

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.inputs.inputs import IntInput, MultilineInput, SecretStrInput
from lfx.schema.data import Data


class GoogleSearchAPIComponent(LCToolComponent):
    """Google 搜索工具组件（弃用）。

    契约：输入查询与 `k`，输出包含 `snippet` 的 `Data` 列表。
    决策：通过外部包装器统一请求逻辑，保持与 LangChain 生态兼容。
    问题：手写 HTTP 逻辑会重复且难以维护。
    方案：复用官方 wrapper 并透出 `results`/`run`。
    代价：对第三方库版本敏感。
    重评：当弃用组件清理或新组件替代后移除此实现。
    """
    display_name = "Google Search API [DEPRECATED]"
    description = "Call Google Search API."
    name = "GoogleSearchAPI"
    icon = "Google"
    legacy = True
    inputs = [
        SecretStrInput(name="google_api_key", display_name="Google API Key", required=True),
        SecretStrInput(name="google_cse_id", display_name="Google CSE ID", required=True),
        MultilineInput(
            name="input_value",
            display_name="Input",
        ),
        IntInput(name="k", display_name="Number of results", value=4, required=True),
    ]

    def run_model(self) -> Data | list[Data]:
        """执行搜索并返回 `Data` 结果列表。"""
        wrapper = self._build_wrapper()
        results = wrapper.results(query=self.input_value, num_results=self.k)
        data = [Data(data=result, text=result["snippet"]) for result in results]
        self.status = data
        return data

    def build_tool(self) -> Tool:
        """构建可被 LangChain 调用的工具实例。"""
        wrapper = self._build_wrapper()
        return Tool(
            name="google_search",
            description="Search Google for recent results.",
            func=wrapper.run,
        )

    def _build_wrapper(self):
        """初始化 Google Search API 包装器。"""
        try:
            from langchain_google_community import GoogleSearchAPIWrapper
        except ImportError as e:
            msg = "Please install langchain-google-community to use GoogleSearchAPIWrapper."
            raise ImportError(msg) from e
        return GoogleSearchAPIWrapper(google_api_key=self.google_api_key, google_cse_id=self.google_cse_id, k=self.k)
