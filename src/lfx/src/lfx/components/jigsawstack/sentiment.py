"""
模块名称：JigsawStack 情感分析组件

本模块调用 JigsawStack 情感分析接口，对文本进行情绪与倾向判定。
主要功能包括：
- 提交文本并获取总体情感、情绪与评分
- 返回句级别分析结果（如可用）
- 统一错误处理与状态更新

关键组件：
- JigsawStackSentimentComponent：情感分析组件入口

设计背景：为 Langflow 提供标准化情感分析能力。
注意事项：依赖 `jigsawstack>=0.2.7` 且需要有效 `api_key`。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.data import Data
from lfx.schema.message import Message


class JigsawStackSentimentComponent(Component):
    """JigsawStack 情感分析组件封装。

    契约：输入为 `text`；输出 `Data` 或 `Message`。
    副作用：网络调用外部情感分析服务并更新 `self.status`。
    失败语义：API 失败抛 `ValueError`；SDK 异常返回失败 `Data`。
    """

    display_name = "Sentiment Analysis"
    description = "Analyze sentiment of text using JigsawStack AI"
    documentation = "https://jigsawstack.com/docs/api-reference/ai/sentiment"
    icon = "JigsawStack"
    name = "JigsawStackSentiment"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        MessageTextInput(
            name="text",
            display_name="Text",
            info="Text to analyze for sentiment",
            required=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Sentiment Data", name="sentiment_data", method="analyze_sentiment"),
        Output(display_name="Sentiment Text", name="sentiment_text", method="get_sentiment_text"),
    ]

    def analyze_sentiment(self) -> Data:
        """返回结构化情感分析结果。

        契约：输入为 `text`，输出 `Data`（含情感、情绪与评分）。
        副作用：触发网络调用并更新 `self.status`。
        失败语义：API 失败抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 调用 `client.sentiment`；
        2) 校验 `success` 并提取 `sentiment`；
        3) 组装标准化输出并更新 `self.status`。
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
            response = client.sentiment({"text": self.text})

            api_error_msg = "JigsawStack API returned unsuccessful response"
            if not response.get("success", False):
                raise ValueError(api_error_msg)

            sentiment_data = response.get("sentiment", {})

            result_data = {
                "text_analyzed": self.text,
                "sentiment": sentiment_data.get("sentiment", "Unknown"),
                "emotion": sentiment_data.get("emotion", "Unknown"),
                "score": sentiment_data.get("score", 0.0),
                "sentences": response.get("sentences", []),
                "success": True,
            }

            self.status = (
                f"Sentiment: {sentiment_data.get('sentiment', 'Unknown')} | "
                f"Emotion: {sentiment_data.get('emotion', 'Unknown')} | "
                f"Score: {sentiment_data.get('score', 0.0):.3f}"
            )

            return Data(data=result_data)

        except JigsawStackError as e:
            error_data = {"error": str(e), "text_analyzed": self.text, "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)

    def get_sentiment_text(self) -> Message:
        """返回可读的情感分析文本。

        契约：输出包含总体情感与逐句结果的 `Message` 文本。
        失败语义：SDK 缺失或 API 异常时返回错误文本。
        副作用：触发网络调用。
        """
        try:
            from jigsawstack import JigsawStack, JigsawStackError
        except ImportError:
            return Message(text="Error: JigsawStack package not found. Please install it with: pip install jigsawstack")

        try:
            client = JigsawStack(api_key=self.api_key)
            response = client.sentiment({"text": self.text})

            sentiment_data = response.get("sentiment", {})
            sentences = response.get("sentences", [])

            # 实现：将总体情感与逐句分析整理为可读文本
            formatted_output = f"""Sentiment Analysis Results:

Text: {self.text}

Overall Sentiment: {sentiment_data.get("sentiment", "Unknown")}
Emotion: {sentiment_data.get("emotion", "Unknown")}
Score: {sentiment_data.get("score", 0.0):.3f}

Sentence-by-sentence Analysis:
"""

            for i, sentence in enumerate(sentences, 1):
                formatted_output += (
                    f"{i}. {sentence.get('text', '')}\n"
                    f"   Sentiment: {sentence.get('sentiment', 'Unknown')} | "
                    f"Emotion: {sentence.get('emotion', 'Unknown')} | "
                    f"Score: {sentence.get('score', 0.0):.3f}\n"
                )

            return Message(text=formatted_output)

        except JigsawStackError as e:
            return Message(text=f"Error analyzing sentiment: {e!s}")
