"""
模块名称：JigsawStack VOCR 组件

本模块封装 JigsawStack `vision.vocr`，用于从图像或文档中提取结构化信息。
主要功能包括：
- 支持提示词列表与逗号分隔字符串
- 支持 URL 或 `file_store_key` 输入源
- 支持文档页码范围选择

关键组件：
- JigsawStackVOCRComponent：VOCR 组件入口

设计背景：为 Langflow 提供统一的视觉文档理解能力。
注意事项：`page_range` 仅在同时提供起止页时生效。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class JigsawStackVOCRComponent(Component):
    """JigsawStack VOCR 组件封装。

    契约：输入为 `url`/`file_store_key` 与可选 `prompts`/`page_range`；输出 `Data`。
    副作用：触发外部 VOCR 请求并更新 `self.status`。
    失败语义：SDK 异常返回失败 `Data`；输入不符合类型约束抛 `ValueError`。
    """

    display_name = "VOCR"
    description = "Extract data from any document type in a consistent structure with fine-tuned \
        vLLMs for the highest accuracy"
    documentation = "https://jigsawstack.com/docs/api-reference/ai/vocr"
    icon = "JigsawStack"
    name = "JigsawStackVOCR"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        MessageTextInput(
            name="prompts",
            display_name="Prompts",
            info="The prompts used to describe the image. Default prompt is Describe the image in detail. \
                You can pass a list of comma-separated prompts to extract different information from the image.",
            required=False,
            tool_mode=True,
        ),
        StrInput(
            name="url",
            display_name="URL",
            info="The image or document url. Not required if file_store_key is specified.",
            required=False,
            tool_mode=True,
        ),
        StrInput(
            name="file_store_key",
            display_name="File Store Key",
            info="The key used to store the image on Jigsawstack File Storage. Not required if url is specified.",
            required=False,
            tool_mode=True,
        ),
        IntInput(
            name="page_range_start",
            display_name="Page Range",
            info="Page range start limit for the document. If not specified, all pages will be processed.",
            required=False,
        ),
        IntInput(
            name="page_range_end",
            display_name="Page Range End",
            info="Page range end limit for the document. If not specified, all pages will be processed.",
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="VOCR results", name="vocr_results", method="vocr"),
    ]

    def vocr(self) -> Data:
        """执行 VOCR 并返回结果。

        契约：输入为 `url`/`file_store_key` 与可选 `prompts`/`page_range`，输出 `Data`。
        副作用：触发外部 VOCR 请求并更新 `self.status`。
        失败语义：提示词类型非法抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 规范化 `prompts` 与输入源；
        2) 组装请求参数（包含可选 `page_range`）；
        3) 调用 `client.vision.vocr` 并校验 `success`。
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

            # 实现：规范化 `prompts`，支持字符串/列表两种形态
            params = {}
            if self.prompts:
                if isinstance(self.prompts, list):
                    params["prompt"] = self.prompts
                elif isinstance(self.prompts, str):
                    if "," in self.prompts:
                        # 实现：逗号分隔的提示词拆分并去除空白
                        params["prompt"] = [p.strip() for p in self.prompts.split(",")]
                    else:
                        params["prompt"] = [self.prompts.strip()]
                else:
                    invalid_prompt_error = "Prompt must be a list of strings or a single string"
                    raise ValueError(invalid_prompt_error)
            if self.url:
                params["url"] = self.url
            if self.file_store_key:
                params["file_store_key"] = self.file_store_key

            if self.page_range_start and self.page_range_end:
                params["page_range"] = [self.page_range_start, self.page_range_end]

            # 实现：调用 JigsawStack VOCR 接口
            response = client.vision.vocr(params)

            if not response.get("success", False):
                failed_response_error = "JigsawStack API returned unsuccessful response"
                raise ValueError(failed_response_error)

            return Data(data=response)

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)
