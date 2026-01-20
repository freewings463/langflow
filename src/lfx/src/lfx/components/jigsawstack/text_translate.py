"""
模块名称：JigsawStack 文本翻译组件

本模块封装 JigsawStack `translate.text`，用于多语言文本翻译。
主要功能包括：
- 支持单条文本或文本列表
- 组装翻译请求参数并返回结果
- 失败语义统一处理

关键组件：
- JigsawStackTextTranslateComponent：文本翻译组件入口

设计背景：为 Langflow 提供多语言翻译能力。
注意事项：`target_language` 必须是 ISO 639-1 两字母代码。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class JigsawStackTextTranslateComponent(Component):
    """JigsawStack 文本翻译组件封装。

    契约：输入为 `target_language` 与 `text`；输出 `Data`。
    副作用：触发外部翻译请求并更新 `self.status`。
    失败语义：SDK 异常返回失败 `Data`；SDK 缺失抛 `ImportError`。
    """

    display_name = "Text Translate"
    description = "Translate text from one language to another with support for multiple text formats."
    documentation = "https://jigsawstack.com/docs/api-reference/ai/translate"
    icon = "JigsawStack"
    name = "JigsawStackTextTranslate"
    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        StrInput(
            name="target_language",
            display_name="Target Language",
            info="The language code of the target language to translate to. \
                Language code is identified by a unique ISO 639-1 two-letter code",
            required=True,
            tool_mode=True,
        ),
        MessageTextInput(
            name="text",
            display_name="Text",
            info="The text to translate. This can be a single string or a list of strings. \
                If a list is provided, each string will be translated separately.",
            required=True,
            is_list=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Translation Results", name="translation_results", method="translation"),
    ]

    def translation(self) -> Data:
        """执行文本翻译并返回结果。

        契约：输入为 `target_language` 与 `text`，输出为 `Data`。
        副作用：触发外部翻译请求并更新 `self.status`。
        失败语义：SDK 异常返回失败 `Data`；SDK 缺失抛 `ImportError`。

        关键路径（三步）：
        1) 规范化 `text` 为列表；
        2) 构建翻译参数并调用 `client.translate.text`；
        3) 校验 `success` 并返回响应。
        """
        try:
            from jigsawstack import JigsawStack, JigsawStackError
        except ImportError as e:
            jigsawstack_import_error = (
                "JigsawStack package not found. Please install it using: pip install jigsawstack>=0.2.7"
            )
            raise ImportError(jigsawstack_import_error) from e

        try:
            client = JigsawStack(api_key=self.api_key)

            # 实现：组装翻译请求参数
            params = {}
            if self.target_language:
                params["target_language"] = self.target_language

            if self.text:
                if isinstance(self.text, list):
                    params["text"] = self.text
                else:
                    params["text"] = [self.text]

            # 实现：调用 JigsawStack 翻译接口
            response = client.translate.text(params)

            if not response.get("success", False):
                failed_response_error = "JigsawStack API returned unsuccessful response"
                raise ValueError(failed_response_error)

            return Data(data=response)

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)
