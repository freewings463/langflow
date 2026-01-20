"""
模块名称：`Google OAuth` Token 组件

本模块提供 `GoogleOAuthToken`，用于生成 `Google` `OAuth` token 的 `JSON` 字符串。
主要功能包括：
- 校验 scope 格式
- 通过本地 `OAuth` 流程获取 `token`
- 将 `token` 写入本地 `token.json`

关键组件：`GoogleOAuthToken`
设计背景：便捷生成 `Google` `OAuth` 凭证供其他组件使用
注意事项：会在本地写入 `token.json`；需提供有效 `credentials` 文件
"""

import json
import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from lfx.custom.custom_component.component import Component
from lfx.io import FileInput, MultilineInput, Output
from lfx.schema.data import Data


class GoogleOAuthToken(Component):
    """`Google OAuth` Token 生成组件。
    契约：输入为 `scopes` 与 `credentials` 文件；输出为 `Data`。
    关键路径：校验 `scopes` → 读取/刷新 `token` → 返回 `JSON`。
    决策：`token` 默认写入 `token.json`。问题：便于复用；方案：本地缓存；代价：落盘风险；重评：当需要内存存储时。
    """

    display_name = "Google OAuth Token"
    description = "Generates a JSON string with your Google OAuth token."
    documentation: str = "https://developers.google.com/identity/protocols/oauth2/web-server?hl=pt-br#python_1"
    icon = "Google"
    name = "GoogleOAuthToken"
    legacy: bool = True
    inputs = [
        MultilineInput(
            name="scopes",
            display_name="Scopes",
            info="Input scopes for your application.",
            required=True,
        ),
        FileInput(
            name="oauth_credentials",
            display_name="Credentials File",
            info="Input OAuth Credentials file (e.g. credentials.json).",
            file_types=["json"],
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="build_output"),
    ]

    def validate_scopes(self, scopes):
        """校验 `scopes` 字符串格式。
        契约：格式非法抛 `ValueError`。
        关键路径：正则匹配 → 失败抛错。
        决策：仅允许白名单模式。问题：避免无效 scope；方案：正则校验；代价：可扩展性差；重评：当支持更多 scope 时。
        """
        pattern = (
            r"^(https://www\.googleapis\.com/auth/[\w\.\-]+"
            r"|mail\.google\.com/"
            r"|www\.google\.com/calendar/feeds"
            r"|www\.google\.com/m8/feeds)"
            r"(,\s*https://www\.googleapis\.com/auth/[\w\.\-]+"
            r"|mail\.google\.com/"
            r"|www\.google\.com/calendar/feeds"
            r"|www\.google\.com/m8/feeds)*$"
        )
        if not re.match(pattern, scopes):
            error_message = "Invalid scope format."
            raise ValueError(error_message)

    def build_output(self) -> Data:
        """生成 `token` 并返回 `Data`。
        契约：返回 `Data(data=creds_json)`；失败抛 `ValueError`。
        关键路径：校验 `scopes` → 读取/刷新 `token` → 走授权流程 → 写入 `token.json`。
        决策：无 `token` 或过期时启动本地授权。问题：确保可用性；方案：`InstalledAppFlow`；代价：需要用户交互；重评：当支持服务账号流程时。
        """
        self.validate_scopes(self.scopes)

        user_scopes = [scope.strip() for scope in self.scopes.split(",")]
        if self.scopes:
            scopes = user_scopes
        else:
            error_message = "Incorrect scope, check the scopes field."
            raise ValueError(error_message)

        creds = None
        token_path = Path("token.json")

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if self.oauth_credentials:
                    client_secret_file = self.oauth_credentials
                else:
                    error_message = "OAuth 2.0 Credentials file not provided."
                    raise ValueError(error_message)

                flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes)
                creds = flow.run_local_server(port=0)

                token_path.write_text(creds.to_json(), encoding="utf-8")

        creds_json = json.loads(creds.to_json())

        return Data(data=creds_json)
