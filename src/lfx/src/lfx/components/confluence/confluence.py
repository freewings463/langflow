"""
模块名称：Confluence 文档加载组件

本模块提供从 Confluence 拉取页面并转换为 `Data` 的组件封装。主要功能包括：
- 配置 Confluence 连接参数与内容格式
- 拉取指定 Space 的页面并转换为 `Data` 列表

关键组件：
- `ConfluenceComponent`

设计背景：需要将 Confluence 作为知识源接入 LFX 流程。
使用场景：知识库构建、文档检索、内容归档。
注意事项：依赖 `langchain_community`，Confluence API 访问失败会抛异常。
"""

from langchain_community.document_loaders import ConfluenceLoader
from langchain_community.document_loaders.confluence import ContentFormat

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class ConfluenceComponent(Component):
    """Confluence 文档加载组件

    契约：输入 Confluence 站点、凭证、Space 与格式参数；输出 `list[Data]`；
    副作用：对 Confluence 发起网络请求并更新 `self.status`；
    失败语义：连接或鉴权失败时抛异常，加载器异常透传。
    关键路径：1) 构建 `ConfluenceLoader` 2) 拉取文档 3) 转换为 `Data`。
    决策：通过 `ContentFormat` 控制内容格式。
    问题：不同格式影响内容结构与后续解析。
    方案：暴露 `content_format` 供用户选择。
    代价：错误格式可能导致文本质量下降。
    重评：当下游统一使用某一格式或需要自动选择时。
    """
    display_name = "Confluence"
    description = "Confluence wiki collaboration platform"
    documentation = "https://python.langchain.com/v0.2/docs/integrations/document_loaders/confluence/"
    trace_type = "tool"
    icon = "Confluence"
    name = "Confluence"

    inputs = [
        StrInput(
            name="url",
            display_name="Site URL",
            required=True,
            info="The base URL of the Confluence Space. Example: https://<company>.atlassian.net/wiki.",
        ),
        StrInput(
            name="username",
            display_name="Username",
            required=True,
            info="Atlassian User E-mail. Example: email@example.com",
        ),
        SecretStrInput(
            name="api_key",
            display_name="Confluence API Key",
            required=True,
            info="Atlassian Key. Create at: https://id.atlassian.com/manage-profile/security/api-tokens",
        ),
        StrInput(name="space_key", display_name="Space Key", required=True),
        BoolInput(name="cloud", display_name="Use Cloud?", required=True, value=True, advanced=True),
        DropdownInput(
            name="content_format",
            display_name="Content Format",
            options=[
                ContentFormat.EDITOR.value,
                ContentFormat.EXPORT_VIEW.value,
                ContentFormat.ANONYMOUS_EXPORT_VIEW.value,
                ContentFormat.STORAGE.value,
                ContentFormat.VIEW.value,
            ],
            value=ContentFormat.STORAGE.value,
            required=True,
            advanced=True,
            info="Specify content format, defaults to ContentFormat.STORAGE",
        ),
        IntInput(
            name="max_pages",
            display_name="Max Pages",
            required=False,
            value=1000,
            advanced=True,
            info="Maximum number of pages to retrieve in total, defaults 1000",
        ),
    ]

    outputs = [
        Output(name="data", display_name="Data", method="load_documents"),
    ]

    def build_confluence(self) -> ConfluenceLoader:
        """构建 Confluence 加载器

        契约：读取组件配置并返回 `ConfluenceLoader`；副作用：无；
        失败语义：参数无效或依赖异常时抛异常。
        关键路径：1) 解析 `content_format` 2) 构建加载器实例。
        决策：在构建阶段转换 `content_format` 为枚举。
        问题：输入为字符串需匹配枚举类型。
        方案：显式转换为 `ContentFormat`。
        代价：非法值会抛异常。
        重评：当输入层已保证类型安全时。
        """
        content_format = ContentFormat(self.content_format)
        return ConfluenceLoader(
            url=self.url,
            username=self.username,
            api_key=self.api_key,
            cloud=self.cloud,
            space_key=self.space_key,
            content_format=content_format,
            max_pages=self.max_pages,
        )

    def load_documents(self) -> list[Data]:
        """加载 Confluence 页面并转换为 `Data`

        契约：调用 `build_confluence` 加载文档并返回 `list[Data]`；
        副作用：网络请求与状态更新；
        失败语义：加载失败异常透传。
        关键路径：1) 构建加载器 2) 拉取文档 3) 转换为 `Data` 并写入状态。
        决策：使用 `Data.from_document` 统一转换。
        问题：需要标准化 LangChain Document 到 LFX `Data`。
        方案：调用已有转换方法。
        代价：转换过程增加额外对象分配。
        重评：当上游直接返回 `Data` 或引入批量转换优化时。
        """
        confluence = self.build_confluence()
        documents = confluence.load()
        data = [Data.from_document(doc) for doc in documents]
        self.status = data
        return data
