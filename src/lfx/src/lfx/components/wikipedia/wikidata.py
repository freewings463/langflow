"""
模块名称：Wikidata 搜索组件

本模块提供 Wikidata API 查询能力，主要用于根据文本查询返回实体列表。主要功能包括：
- 组装查询参数并调用 Wikidata 搜索接口
- 将结果转换为 `Data`/`DataFrame`
- 对 HTTP 与解析错误进行统一异常封装

关键组件：
- `WikidataComponent`：组件主体
- `fetch_content`：调用 API 并生成 `Data` 列表
- `fetch_content_dataframe`：返回 `DataFrame` 结果

设计背景：为流程提供轻量的实体检索入口。
使用场景：根据文本查询返回实体 ID、描述与链接。
注意事项：请求失败会抛 `ToolException`；未命中结果会返回包含错误信息的 `Data`。
"""

import httpx
from httpx import HTTPError
from langchain_core.tools import ToolException

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MultilineInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class WikidataComponent(Component):
    """Wikidata 搜索组件。

    契约：输入 `query`；输出 `DataFrame`（内部由 `Data` 列表构造）。
    副作用：发起网络请求，并更新 `self.status`。
    失败语义：HTTP/解析错误抛 `ToolException`；无结果返回带 `error` 的 `Data`。
    关键路径：1) 组装参数并请求 2) 解析结果 3) 转换为 `Data`/`DataFrame`。
    决策：使用 Wikidata 官方 API 而非网页抓取。
    问题：需要稳定的结构化实体检索结果。
    方案：调用 `w/api.php` 的 `wbsearchentities`。
    代价：受 API 配额与网络延迟影响。
    重评：当 API 受限或字段不足时考虑备用来源。
    """
    display_name = "Wikidata"
    description = "Performs a search using the Wikidata API."
    icon = "Wikipedia"

    inputs = [
        MultilineInput(
            name="query",
            display_name="Query",
            info="The text query for similarity search on Wikidata.",
            required=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def run_model(self) -> DataFrame:
        """运行组件主逻辑并返回 `DataFrame`。"""
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """调用 Wikidata API 并返回 `Data` 列表。

        契约：`query` 不能为空；每条 `Data` 包含 label、id、url 等字段。
        副作用：发起 HTTP 请求。
        失败语义：HTTP/解析异常抛 `ToolException`；无结果返回带 `error` 的 `Data` 列表。
        """
        try:
            # 实现：按 Wikidata API 要求构造查询参数。
            params = {
                "action": "wbsearchentities",
                "format": "json",
                "search": self.query,
                "language": "en",
            }

            # 实现：请求 Wikidata 搜索接口。
            wikidata_api_url = "https://www.wikidata.org/w/api.php"
            response = httpx.get(wikidata_api_url, params=params)
            response.raise_for_status()
            response_json = response.json()

            # 实现：提取搜索结果列表。
            results = response_json.get("search", [])

            if not results:
                return [Data(data={"error": "No search results found for the given query."})]

            # 实现：将 API 响应转换为 `Data` 列表。
            data = [
                Data(
                    text=f"{result['label']}: {result.get('description', '')}",
                    data={
                        "label": result["label"],
                        "id": result.get("id"),
                        "url": result.get("url"),
                        "description": result.get("description", ""),
                        "concepturi": result.get("concepturi"),
                    },
                )
                for result in results
            ]

            self.status = data
        except HTTPError as e:
            error_message = f"HTTP Error in Wikidata Search API: {e!s}"
            raise ToolException(error_message) from None
        except KeyError as e:
            error_message = f"Data parsing error in Wikidata API response: {e!s}"
            raise ToolException(error_message) from None
        except ValueError as e:
            error_message = f"Value error in Wikidata API: {e!s}"
            raise ToolException(error_message) from None
        else:
            return data

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果转换为 `DataFrame` 返回。"""
        data = self.fetch_content()
        return DataFrame(data)
