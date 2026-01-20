"""
模块名称：JigsawStack AI Scraper 组件

本模块提供面向 Langflow 的网页结构化抓取组件，封装 JigsawStack `web.ai_scrape`。
主要功能包括：
- 参数校验：`url`/`html` 二选一，`element_prompts` 最多 5 条
- 请求组装：仅发送非空字段，避免 SDK 处理空值
- 失败语义：API `success=False` 返回失败结果并设置 `self.status`

关键组件：
- JigsawStackAIScraperComponent：AI Scraper 组件入口

设计背景：统一 Langflow 组件形态并对接 JigsawStack SDK。
注意事项：依赖 `jigsawstack>=0.2.7`，需提供有效 `api_key`。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.data import Data

MAX_ELEMENT_PROMPTS = 5


class JigsawStackAIScraperComponent(Component):
    """JigsawStack AI Scraper 组件封装。

    契约：输入由 `inputs` 定义（`url`/`html`/`element_prompts`），输出 `Data`。
    副作用：触发外部网络请求并更新 `self.status`。
    失败语义：参数缺失抛 `ValueError`；SDK 缺失抛 `ImportError`；SDK 异常返回失败 `Data`。
    """

    display_name = "AI Scraper"
    description = "Scrape any website instantly and get consistent structured data \
        in seconds without writing any css selector code"
    documentation = "https://jigsawstack.com/docs/api-reference/ai/scrape"
    icon = "JigsawStack"
    name = "JigsawStackAIScraper"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        MessageTextInput(
            name="url",
            display_name="URL",
            info="URL of the page to scrape. Either url or html is required, but not both.",
            required=False,
            tool_mode=True,
        ),
        MessageTextInput(
            name="html",
            display_name="HTML",
            info="HTML content to scrape. Either url or html is required, but not both.",
            required=False,
            tool_mode=True,
        ),
        MessageTextInput(
            name="element_prompts",
            display_name="Element Prompts",
            info="Items on the page to be scraped (maximum 5). E.g. 'Plan price', 'Plan title'",
            required=True,
            tool_mode=True,
        ),
        MessageTextInput(
            name="root_element_selector",
            display_name="Root Element Selector",
            info="CSS selector to limit the scope of scraping to a specific element and its children",
            required=False,
            value="main",
        ),
    ]

    outputs = [
        Output(display_name="AI Scraper Results", name="scrape_results", method="scrape"),
    ]

    def scrape(self) -> Data:
        """执行 AI Scrape 并返回结构化结果。

        契约：输入使用 `url`/`html`/`element_prompts`，输出为 `Data`。
        副作用：触发网络请求并写入 `self.status`。
        失败语义：参数缺失抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 校验并规范化 `url`/`html`/`element_prompts`；
        2) 组装请求并调用 `client.web.ai_scrape`；
        3) 校验响应 `success` 并更新 `self.status`。

        异常流：`JigsawStackError` -> 返回失败 `Data`；输入缺失 -> 抛 `ValueError`。
        排障入口：`self.status` 文本。
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

            # 实现：仅在字段非空时填充请求参数，避免 SDK 处理空值
            scrape_params: dict = {}
            if self.url:
                scrape_params["url"] = self.url
            if self.html:
                scrape_params["html"] = self.html

            url_value = scrape_params.get("url", "")
            html_value = scrape_params.get("html", "")
            if (not url_value or not url_value.strip()) and (not html_value or not html_value.strip()):
                url_or_html_error = "Either 'url' or 'html' must be provided for scraping"
                raise ValueError(url_or_html_error)

            # 注意：`element_prompts` 支持字符串或列表，且最多 5 条
            element_prompts_list: list[str] = []
            if self.element_prompts:
                element_prompts_value: str | list[str] = self.element_prompts

                if isinstance(element_prompts_value, str):
                    if "," not in element_prompts_value:
                        element_prompts_list = [element_prompts_value]
                    else:
                        element_prompts_list = element_prompts_value.split(",")
                elif isinstance(element_prompts_value, list):
                    element_prompts_list = element_prompts_value
                else:
                    # 实现：兜底将未知类型按字符串拆分
                    element_prompts_list = str(element_prompts_value).split(",")

                if len(element_prompts_list) > MAX_ELEMENT_PROMPTS:
                    max_elements_error = "Maximum of 5 element prompts allowed"
                    raise ValueError(max_elements_error)
                if len(element_prompts_list) == 0:
                    invalid_elements_error = "Element prompts cannot be empty"
                    raise ValueError(invalid_elements_error)

                scrape_params["element_prompts"] = element_prompts_list

            if self.root_element_selector:
                scrape_params["root_element_selector"] = self.root_element_selector

            # 实现：调用 JigsawStack AI Scrape
            response = client.web.ai_scrape(scrape_params)

            if not response.get("success", False):
                fail_error = "JigsawStack API request failed."
                raise ValueError(fail_error)

            result_data = response

            self.status = "AI scrape process is now complete."

            return Data(data=result_data)

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)
