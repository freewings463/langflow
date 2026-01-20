"""
模块名称：`Google Search API` 组件

本模块提供 `GoogleSearchAPICore`，用于调用 `Google Search API` 并返回 `DataFrame`。
主要功能包括：
- 校验 API Key/CSE ID
- 调用 `GoogleSearchAPIWrapper` 获取结果
- 将结果转换为 `DataFrame`

关键组件：`GoogleSearchAPICore`
设计背景：统一 `Google` 搜索能力接入
注意事项：无效凭证会返回包含错误信息的 `DataFrame`
"""

from langchain_google_community import GoogleSearchAPIWrapper

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MultilineInput, Output, SecretStrInput
from lfx.schema.dataframe import DataFrame


class GoogleSearchAPICore(Component):
    """`Google Search API` 组件。
    契约：输入为 `API Key`/`CSE ID` 与查询；输出为 `DataFrame`。
    关键路径：校验凭证 → 调用 `wrapper` → 返回结果。
    决策：错误以 `DataFrame` 返回。问题：保持输出类型稳定；方案：错误行返回；代价：下游需解析错误字段；重评：当需要异常流时。
    """

    display_name = "Google Search API"
    description = "Call Google Search API and return results as a DataFrame."
    icon = "Google"

    inputs = [
        SecretStrInput(
            name="google_api_key",
            display_name="Google API Key",
            required=True,
        ),
        SecretStrInput(
            name="google_cse_id",
            display_name="Google CSE ID",
            required=True,
        ),
        MultilineInput(
            name="input_value",
            display_name="Input",
            tool_mode=True,
        ),
        IntInput(
            name="k",
            display_name="Number of results",
            value=4,
            required=True,
        ),
    ]

    outputs = [
        Output(
            display_name="Results",
            name="results",
            type_=DataFrame,
            method="search_google",
        ),
    ]

    def search_google(self) -> DataFrame:
        """执行搜索并返回 `DataFrame`。
        契约：成功返回结果；无效凭证返回错误 `DataFrame`。
        关键路径：校验 `Key`/`CSE` → 调用 `wrapper` → 构建 `DataFrame`。
        决策：捕获异常并转为错误行。问题：避免抛错中断流程；方案：错误行；代价：隐藏异常；重评：当需要严格错误处理时。
        """
        if not self.google_api_key:
            return DataFrame([{"error": "Invalid Google API Key"}])

        if not self.google_cse_id:
            return DataFrame([{"error": "Invalid Google CSE ID"}])

        try:
            wrapper = GoogleSearchAPIWrapper(
                google_api_key=self.google_api_key, google_cse_id=self.google_cse_id, k=self.k
            )
            results = wrapper.results(query=self.input_value, num_results=self.k)
            return DataFrame(results)
        except (ValueError, KeyError) as e:
            return DataFrame([{"error": f"Invalid configuration: {e!s}"}])
        except ConnectionError as e:
            return DataFrame([{"error": f"Connection error: {e!s}"}])
        except RuntimeError as e:
            return DataFrame([{"error": f"Error occurred while searching: {e!s}"}])

    def build(self):
        """返回可调用的搜索函数。
        契约：返回 `search_google`。
        关键路径：直接返回方法引用。
        决策：以 `build` 暴露执行入口。问题：符合组件约定；方案：返回方法；代价：无额外控制；重评：当需要异步入口时。
        """
        return self.search_google
