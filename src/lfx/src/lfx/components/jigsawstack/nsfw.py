"""
模块名称：JigsawStack NSFW 检测组件

本模块调用 JigsawStack `validate.nsfw` 对图片/视频进行敏感内容检测。
主要功能包括：
- 构建 NSFW 检测请求
- 校验响应并返回结构化结果
- 统一错误处理并反馈状态

关键组件：
- JigsawStackNSFWComponent：NSFW 检测组件入口

设计背景：为 Langflow 提供可复用的内容安全检测能力。
注意事项：仅支持 URL 输入，需确保目标资源可访问。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class JigsawStackNSFWComponent(Component):
    """JigsawStack NSFW 检测组件封装。

    契约：输入为 `url`；输出 `Data`。
    副作用：触发外部网络请求并更新 `self.status`。
    失败语义：API 失败抛 `ValueError`；SDK 异常返回失败 `Data`。
    """

    display_name = "NSFW Detection"
    description = "Detect if image/video contains NSFW content"
    documentation = "https://jigsawstack.com/docs/api-reference/ai/nsfw"
    icon = "JigsawStack"
    name = "JigsawStackNSFW"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        StrInput(
            name="url",
            display_name="URL",
            info="URL of the image or video to analyze",
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="NSFW Analysis", name="nsfw_result", method="detect_nsfw"),
    ]

    def detect_nsfw(self) -> Data:
        """执行 NSFW 检测并返回结果。

        契约：输入为 `url`，输出为 `Data`。
        副作用：触发外部检测请求并更新 `self.status`。
        失败语义：API 失败抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 构建 `url` 参数；
        2) 调用 `client.validate.nsfw`；
        3) 校验 `success` 并返回结果。
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

            # 实现：仅包含 `url` 字段的检测请求
            params = {"url": self.url}

            response = client.validate.nsfw(params)

            api_error_msg = "JigsawStack API returned unsuccessful response"
            if not response.get("success", False):
                raise ValueError(api_error_msg)

            return Data(data=response)

        except ValueError:
            raise
        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)
