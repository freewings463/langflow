"""
模块名称：arXiv 搜索组件

本模块提供 arXiv.org 的查询与结果解析能力，主要用于在 Langflow 中检索论文并输出为
DataFrame。主要功能包括：
- 构建 arXiv API 查询 URL 并执行请求
- 解析 Atom XML 响应并结构化为字典
- 将结果封装为 `Data` 与 `DataFrame`

关键组件：
- `ArXivComponent`：对外组件入口与查询逻辑

设计背景：为组件化流程提供轻量的学术论文检索能力。
注意事项：仅允许访问 `export.arxiv.org`，非法 URL 将被拒绝。
"""

import urllib.request
from urllib.parse import urlparse
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, IntInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class ArXivComponent(Component):
    display_name = "arXiv"
    description = "Search and retrieve papers from arXiv.org"
    icon = "arXiv"

    inputs = [
        MessageTextInput(
            name="search_query",
            display_name="Search Query",
            info="The search query for arXiv papers (e.g., 'quantum computing')",
            tool_mode=True,
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Field",
            info="The field to search in",
            options=["all", "title", "abstract", "author", "cat"],
            value="all",
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            info="Maximum number of results to return",
            value=10,
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="search_papers_dataframe"),
    ]

    def build_query_url(self) -> str:
        """构建 arXiv API 查询 URL。

        契约：输入为组件字段 `search_query/search_type/max_results`，输出完整查询 URL。
        关键路径（三步）：
        1) 将搜索类型映射为 arXiv 查询前缀。
        2) 组装查询参数并进行 URL 编码。
        3) 返回可请求的查询 URL。

        失败语义：无（参数异常由上游字段约束处理）。
        """
        base_url = "http://export.arxiv.org/api/query?"

        if self.search_type == "all":
            search_query = self.search_query
        else:
            prefix_map = {"title": "ti", "abstract": "abs", "author": "au", "cat": "cat"}
            prefix = prefix_map.get(self.search_type, "")
            search_query = f"{prefix}:{self.search_query}"

        params = {
            "search_query": search_query,
            "max_results": str(self.max_results),
        }

        query_string = "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])

        return base_url + query_string

    def parse_atom_response(self, response_text: str) -> list[dict]:
        """解析 arXiv Atom XML 响应。

        契约：输入 XML 字符串，输出论文条目列表。
        副作用：无。
        失败语义：XML 解析失败将抛出异常。
        """
        root = fromstring(response_text)

        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

        papers = []
        for entry in root.findall("atom:entry", ns):
            paper = {
                "id": self._get_text(entry, "atom:id", ns),
                "title": self._get_text(entry, "atom:title", ns),
                "summary": self._get_text(entry, "atom:summary", ns),
                "published": self._get_text(entry, "atom:published", ns),
                "updated": self._get_text(entry, "atom:updated", ns),
                "authors": [author.find("atom:name", ns).text for author in entry.findall("atom:author", ns)],
                "arxiv_url": self._get_link(entry, "alternate", ns),
                "pdf_url": self._get_link(entry, "related", ns),
                "comment": self._get_text(entry, "arxiv:comment", ns),
                "journal_ref": self._get_text(entry, "arxiv:journal_ref", ns),
                "primary_category": self._get_category(entry, ns),
                "categories": [cat.get("term") for cat in entry.findall("atom:category", ns)],
            }
            papers.append(paper)

        return papers

    def _get_text(self, element: Element, path: str, ns: dict) -> str | None:
        """安全提取 XML 节点文本。

        契约：输入元素与路径，输出文本或 `None`。
        副作用：无。
        失败语义：路径不存在或空文本返回 `None`。
        """
        el = element.find(path, ns)
        return el.text.strip() if el is not None and el.text else None

    def _get_link(self, element: Element, rel: str, ns: dict) -> str | None:
        """按 `rel` 获取链接地址。

        契约：输入元素与 `rel`，输出链接 URL 或 `None`。
        副作用：无。
        失败语义：未命中返回 `None`。
        """
        for link in element.findall("atom:link", ns):
            if link.get("rel") == rel:
                return link.get("href")
        return None

    def _get_category(self, element: Element, ns: dict) -> str | None:
        """获取主分类标签。

        契约：输入元素，输出主分类 `term` 或 `None`。
        副作用：无。
        失败语义：缺失主分类时返回 `None`。
        """
        cat = element.find("arxiv:primary_category", ns)
        return cat.get("term") if cat is not None else None

    def run_model(self) -> DataFrame:
        return self.search_papers_dataframe()

    def search_papers(self) -> list[Data]:
        """查询 arXiv 并返回结果。

        契约：输出 `Data` 列表，失败时返回仅包含错误信息的列表。
        关键路径（三步）：
        1) 构建查询 URL 并校验协议与域名。
        2) 使用受限 opener 发起请求并读取响应。
        3) 解析响应并封装为 `Data`。

        异常流：网络错误或 URL 校验失败返回错误 `Data`。
        排障入口：错误消息前缀 `Request error`。
        """
        try:
            url = self.build_query_url()

            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"http", "https"}:
                error_msg = f"Invalid URL scheme: {parsed_url.scheme}"
                raise ValueError(error_msg)
            if parsed_url.hostname != "export.arxiv.org":
                error_msg = f"Invalid host: {parsed_url.hostname}"
                raise ValueError(error_msg)

            class RestrictedHTTPHandler(urllib.request.HTTPHandler):
                def http_open(self, req):
                    return super().http_open(req)

            class RestrictedHTTPSHandler(urllib.request.HTTPSHandler):
                def https_open(self, req):
                    return super().https_open(req)

            opener = urllib.request.build_opener(RestrictedHTTPHandler, RestrictedHTTPSHandler)
            urllib.request.install_opener(opener)

            response = opener.open(url)
            response_text = response.read().decode("utf-8")

            papers = self.parse_atom_response(response_text)

            results = [Data(data=paper) for paper in papers]
            self.status = results
        except (urllib.error.URLError, ValueError) as e:
            error_data = Data(data={"error": f"Request error: {e!s}"})
            self.status = error_data
            return [error_data]
        else:
            return results

    def search_papers_dataframe(self) -> DataFrame:
        """将查询结果转换为 DataFrame。

        契约：输出 `DataFrame`，内部调用 `search_papers`。
        副作用：继承 `search_papers` 的网络请求与状态更新。
        失败语义：错误会体现在 `DataFrame` 中的错误行。
        """
        data = self.search_papers()
        return DataFrame(data)
