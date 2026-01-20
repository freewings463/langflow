"""
模块名称：`Google Drive` 搜索组件

本模块提供 `GoogleDriveSearchComponent`，用于按查询条件检索 `Drive` 文件并返回结果。
主要功能包括：
- 生成查询字符串并支持用户编辑
- 调用 `Drive` `API` 搜索文件
- 返回 `URL`/`ID`/标题等结构化输出

关键组件：`GoogleDriveSearchComponent`
设计背景：为 `Drive` 文件检索提供统一组件入口
注意事项：需要有效 `OAuth` `token`；查询语法需符合 `Drive` `API` 规则
"""

import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DropdownInput, MessageTextInput
from lfx.io import SecretStrInput
from lfx.schema.data import Data
from lfx.template.field.base import Output


class GoogleDriveSearchComponent(Component):
    """`Google Drive` 文件搜索组件。
    契约：输入为 `token` 与查询条件；输出为 `URL`/`ID`/标题或 `Data`。
    关键路径：生成查询 → 调用 `API` → 组装结果。
    决策：默认 `pageSize=5`。问题：避免过大返回；方案：固定数量；代价：结果可能不足；重评：当支持自定义数量时。
    """

    display_name = "Google Drive Search"
    description = "Searches Google Drive files using provided credentials and query parameters."
    icon = "Google"
    legacy: bool = True

    inputs = [
        SecretStrInput(
            name="token_string",
            display_name="Token String",
            info="JSON string containing OAuth 2.0 access token information for service account access",
            required=True,
        ),
        DropdownInput(
            name="query_item",
            display_name="Query Item",
            options=[
                "name",
                "fullText",
                "mimeType",
                "modifiedTime",
                "viewedByMeTime",
                "trashed",
                "starred",
                "parents",
                "owners",
                "writers",
                "readers",
                "sharedWithMe",
                "createdTime",
                "properties",
                "appProperties",
                "visibility",
                "shortcutDetails.targetId",
            ],
            info="The field to query.",
            required=True,
        ),
        DropdownInput(
            name="valid_operator",
            display_name="Valid Operator",
            options=["contains", "=", "!=", "<=", "<", ">", ">=", "in", "has"],
            info="Operator to use in the query.",
            required=True,
        ),
        MessageTextInput(
            name="search_term",
            display_name="Search Term",
            info="The value to search for in the specified query item.",
            required=True,
        ),
        MessageTextInput(
            name="query_string",
            display_name="Query String",
            info="The query string used for searching. You can edit this manually.",
            value="",  # 注意：此字段会被自动生成的查询覆盖
        ),
    ]

    outputs = [
        Output(display_name="Document URLs", name="doc_urls", method="search_doc_urls"),
        Output(display_name="Document IDs", name="doc_ids", method="search_doc_ids"),
        Output(display_name="Document Titles", name="doc_titles", method="search_doc_titles"),
        Output(display_name="Data", name="Data", method="search_data"),
    ]

    def generate_query_string(self) -> str:
        """生成查询字符串并写回输入字段。
        契约：返回查询字符串；同步更新 `query_string`。
        关键路径：读取字段 → 拼接语句 → 写回 `query_string`。
        决策：以 `query_item`/`valid_operator`/`search_term` 组合。问题：简化输入；方案：模板拼接；代价：复杂查询受限；重评：当需要高级语法时。
        """
        query_item = self.query_item
        valid_operator = self.valid_operator
        search_term = self.search_term

        # 注意：拼接查询字符串。
        query = f"{query_item} {valid_operator} '{search_term}'"

        # 注意：同步写回可编辑的查询字符串。
        self.query_string = query

        return query

    def on_inputs_changed(self) -> None:
        """输入变更时自动更新查询字符串。
        契约：更新内部 `query_string` 字段。
        关键路径：触发 `generate_query_string`。
        决策：无条件重建查询。问题：保证输入一致性；方案：自动刷新；代价：可能覆盖手动编辑；重评：当需要保留手动输入时。
        """
        # 注意：输入变更即刷新查询字符串。
        self.generate_query_string()

    def generate_file_url(self, file_id: str, mime_type: str) -> str:
        """根据 `mime_type` 生成对应的 `Drive` 文件 `URL`。
        契约：返回可访问的文件 `URL`。
        关键路径：按 `MIME` 类型映射 `URL` 模板。
        决策：未知类型回退到通用下载链接。问题：保证可访问性；方案：默认模板；代价：可能非最佳体验；重评：当补全更多类型时。
        """
        return {
            "application/vnd.google-apps.document": f"https://docs.google.com/document/d/{file_id}/edit",
            "application/vnd.google-apps.spreadsheet": f"https://docs.google.com/spreadsheets/d/{file_id}/edit",
            "application/vnd.google-apps.presentation": f"https://docs.google.com/presentation/d/{file_id}/edit",
            "application/vnd.google-apps.drawing": f"https://docs.google.com/drawings/d/{file_id}/edit",
            "application/pdf": f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk",
        }.get(mime_type, f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk")

    def search_files(self) -> dict:
        """执行搜索并返回结构化结果字典。
        契约：返回包含 `URL`/`ID`/标题的字典。
        关键路径：解析 `token` → 构建查询 → 调用 `API` → 组装结果。
        决策：若 `query_string` 为空则自动生成。问题：避免空查询；方案：自动生成；代价：可能覆盖用户意图；重评：当需要强制用户输入时。
        """
        # 注意：从 `JSON` 字符串解析 `token`。
        token_info = json.loads(self.token_string)
        creds = Credentials.from_authorized_user_info(token_info)

        # 注意：优先使用用户编辑的查询字符串。
        query = self.query_string or self.generate_query_string()

        # 注意：初始化 `Drive` `API` 客户端。
        service = build("drive", "v3", credentials=creds)

        # 注意：执行搜索请求。
        results = service.files().list(q=query, pageSize=5, fields="nextPageToken, files(id, name, mimeType)").execute()
        items = results.get("files", [])

        doc_urls = []
        doc_ids = []
        doc_titles_urls = []
        doc_titles = []

        if items:
            for item in items:
                # 注意：使用 `ID`/标题/`MIME` 生成 `URL`。
                file_id = item["id"]
                file_title = item["name"]
                mime_type = item["mimeType"]
                file_url = self.generate_file_url(file_id, mime_type)

                # 注意：收集 `URL`/`ID`/标题与标题+`URL`。
                doc_urls.append(file_url)
                doc_ids.append(file_id)
                doc_titles.append(file_title)
                doc_titles_urls.append({"title": file_title, "url": file_url})

        return {"doc_urls": doc_urls, "doc_ids": doc_ids, "doc_titles_urls": doc_titles_urls, "doc_titles": doc_titles}

    def search_doc_ids(self) -> list[str]:
        """返回搜索到的文件 `ID` 列表。
        契约：返回 `ID` 列表；无结果时为空列表。
        关键路径：调用 `search_files` → 提取 `doc_ids`。
        决策：每次调用都会触发搜索。问题：结果实时性；方案：即时查询；代价：重复调用成本；重评：当需要缓存时。
        """
        return self.search_files()["doc_ids"]

    def search_doc_urls(self) -> list[str]:
        """返回搜索到的文件 `URL` 列表。
        契约：返回 `URL` 列表；无结果时为空列表。
        关键路径：调用 `search_files` → 提取 `doc_urls`。
        决策：即时查询。问题：避免缓存过期；方案：实时查询；代价：重复请求；重评：当性能成为瓶颈时。
        """
        return self.search_files()["doc_urls"]

    def search_doc_titles(self) -> list[str]:
        """返回搜索到的文件标题列表。
        契约：返回标题列表；无结果时为空列表。
        关键路径：调用 `search_files` → 提取 `doc_titles`。
        决策：即时查询。问题：避免缓存过期；方案：实时查询；代价：重复请求；重评：当性能成为瓶颈时。
        """
        return self.search_files()["doc_titles"]

    def search_data(self) -> Data:
        """返回包含标题与 `URL` 的 `Data`。
        契约：返回 `Data(data={"text": doc_titles_urls})`。
        关键路径：调用 `search_files` → 包装为 `Data`。
        决策：将标题+`URL` 作为文本字段。问题：便于展示与下游处理；方案：结构化列表；代价：文本字段语义不强；重评：当需要更丰富结构时。
        """
        return Data(data={"text": self.search_files()["doc_titles_urls"]})
