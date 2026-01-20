"""
模块名称：`Google Drive` 加载组件

本模块提供 `GoogleDriveComponent`，用于通过 `OAuth` 凭证加载指定 `Drive` 文档。
主要功能包括：
- 解析凭证 JSON 并构建 `Credentials`
- 调用 `GoogleDriveLoader` 获取文档
- 返回 `Data` 结构化结果

关键组件：`GoogleDriveComponent`
设计背景：统一 `Google Drive` 文档加载能力接入
注意事项：仅支持单个文档 `ID`；凭证错误会导致刷新失败
"""

import json
from json.decoder import JSONDecodeError

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from langchain_google_community import GoogleDriveLoader

from lfx.custom.custom_component.component import Component
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import MessageTextInput
from lfx.io import SecretStrInput
from lfx.schema.data import Data
from lfx.template.field.base import Output


class GoogleDriveComponent(Component):
    """`Google Drive` 文档加载组件。
    契约：输入为 `OAuth` 凭证与文档 `ID`；输出为 `Data`。
    关键路径：解析凭证 → 构建 loader → 加载文档 → 转换为 `Data`。
    决策：限制单个文档加载。问题：避免批量处理复杂性；方案：单文档；代价：效率较低；重评：当支持批量加载时。
    """

    display_name = "Google Drive Loader"
    description = "Loads documents from Google Drive using provided credentials."
    icon = "Google"
    legacy: bool = True

    inputs = [
        SecretStrInput(
            name="json_string",
            display_name="JSON String of the Service Account Token",
            info="JSON string containing OAuth 2.0 access token information for service account access",
            required=True,
        ),
        MessageTextInput(
            name="document_id", display_name="Document ID", info="Single Google Drive document ID", required=True
        ),
    ]

    outputs = [
        Output(display_name="Loaded Documents", name="docs", method="load_documents"),
    ]

    def load_documents(self) -> Data:
        """加载文档并返回 `Data`。
        契约：成功返回 `Data(data={"text": data})`；失败抛 `ValueError`。
        关键路径：解析凭证 → 初始化 loader → 拉取文档 → 结构化输出。
        决策：文档数量不等于 1 直接失败。问题：确保输出一致性；方案：严格校验；代价：无法处理多文档；重评：当支持多文档时。
        """
        class CustomGoogleDriveLoader(GoogleDriveLoader):
            creds: Credentials | None = None
            """直接注入的 `Credentials`。"""

            def _load_credentials(self):
                """加载凭证。
                契约：优先使用 `creds`，缺失则抛 `ValueError`。
                关键路径：检查 `creds` → 返回或报错。
                决策：不回退到父类加载。问题：避免隐式凭证来源；方案：显式注入；代价：调用方必须提供；重评：当需要自动发现凭证时。
                """
                if self.creds:
                    return self.creds
                msg = "No credentials provided."
                raise ValueError(msg)

            class Config:
                arbitrary_types_allowed = True

        json_string = self.json_string

        document_ids = [self.document_id]
        if len(document_ids) != 1:
            msg = "Expected a single document ID"
            raise ValueError(msg)

        # TODO：添加文档 `ID` 的合法性校验

        # 注意：从 `JSON` 字符串解析凭证。
        try:
            token_info = json.loads(json_string)
        except JSONDecodeError as e:
            msg = "Invalid JSON string"
            raise ValueError(msg) from e

        # 注意：使用凭证与文档 `ID` 初始化 loader。
        loader = CustomGoogleDriveLoader(
            creds=Credentials.from_authorized_user_info(token_info), document_ids=document_ids
        )

        # 注意：加载文档内容。
        try:
            docs = loader.load()
        # 注意：捕获 `google.auth.exceptions.RefreshError`。
        except RefreshError as e:
            msg = "Authentication error: Unable to refresh authentication token. Please try to reauthenticate."
            raise ValueError(msg) from e
        except Exception as e:
            msg = f"Error loading documents: {e}"
            raise ValueError(msg) from e

        if len(docs) != 1:
            msg = "Expected a single document to be loaded."
            raise ValueError(msg)

        data = docs_to_data(docs)
        # 注意：返回加载后的 `Data`。
        self.status = data
        return Data(data={"text": data})
