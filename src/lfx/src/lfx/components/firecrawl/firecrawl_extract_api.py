"""
模块名称：firecrawl_extract_api

本模块提供 Firecrawl Extract API 组件，用于从一组 URL 中抽取结构化数据。
主要功能包括：
- 功能1：解析与校验 URL 列表与 prompt。
- 功能2：按需附加 schema 并调用 Extract API。

使用场景：从多个网页中抽取结构化字段并返回统一数据结构。
关键组件：
- 类 `FirecrawlExtractApi`

设计背景：将 Extract API 封装为组件，简化流程内调用。
注意事项：prompt 会被增强以鼓励全面提取；schema 无效时会被跳过。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DataInput, MultilineInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class FirecrawlExtractApi(Component):
    """Firecrawl Extract API 组件。

    契约：输入 `api_key/urls/prompt`；输出 `Data` 包含提取结果。
    关键路径：
    1) 校验 API Key、URLs 与 prompt；
    2) 规范化 URL 列表并增强 prompt；
    3) 构建参数并调用 `extract`。
    异常流：参数缺失或调用失败抛 `ValueError`。
    排障入口：日志记录 schema 解析异常；异常信息包含在抛错中。
    决策：
    问题：输入 prompt 往往过短，提取结果不完整。
    方案：在未提到 schema 时追加“全面提取”提示。
    代价：可能增加返回数据量与成本。
    重评：当调用方提供严格 schema 或限制提示时。
    """
    display_name: str = "Firecrawl Extract API"
    description: str = "Extracts data from a URL."
    name = "FirecrawlExtractApi"

    documentation: str = "https://docs.firecrawl.dev/api-reference/endpoint/extract"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Firecrawl API Key",
            required=True,
            password=True,
            info="The API key to use Firecrawl API.",
        ),
        MultilineInput(
            name="urls",
            display_name="URLs",
            required=True,
            info="List of URLs to extract data from (separated by commas or new lines).",
            tool_mode=True,
        ),
        MultilineInput(
            name="prompt",
            display_name="Prompt",
            required=True,
            info="Prompt to guide the extraction process.",
            tool_mode=True,
        ),
        DataInput(
            name="schema",
            display_name="Schema",
            required=False,
            info="Schema to define the structure of the extracted data.",
        ),
        BoolInput(
            name="enable_web_search",
            display_name="Enable Web Search",
            info="When true, the extraction will use web search to find additional data.",
        ),
        # # 可选项：基础抽取非必须
        # BoolInput(
        #     name="ignore_sitemap",
        #     display_name="Ignore Sitemap",
        #     info="为 true 时，扫描网站时将忽略 sitemap.xml。",
        # ),
        # # 可选项：基础抽取非必须
        # BoolInput(
        #     name="include_subdomains",
        #     display_name="Include Subdomains",
        #     info="为 true 时，扫描时包含子域名。",
        # ),
        # # 可选项：基础抽取非必须
        # BoolInput(
        #     name="show_sources",
        #     display_name="Show Sources",
        #     info="为 true 时，响应中包含抽取所用来源信息。",
        # ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="extract"),
    ]

    def extract(self) -> Data:
        """执行 Extract 并返回结果数据。

        契约：返回 `Data(data=extract_result)`。
        关键路径：校验输入 -> 处理 prompt/schema -> 调用 API。
        异常流：依赖缺失抛 `ImportError`；调用失败抛 `ValueError`。
        决策：
        问题：无效 schema 不应阻断基础抽取。
        方案：schema 校验失败时记录日志并跳过。
        代价：输出结构可能不符合期望 schema。
        重评：当需要严格 schema 校验时。
        """
        try:
            from firecrawl import FirecrawlApp
        except ImportError as e:
            msg = "Could not import firecrawl integration package. Please install it with `pip install firecrawl-py`."
            raise ImportError(msg) from e

        # 注意：API Key 为空会导致请求被拒绝。
        if not self.api_key:
            msg = "API key is required"
            raise ValueError(msg)

        # 注意：URL 列表为空无法执行抽取。
        if not self.urls:
            msg = "URLs are required"
            raise ValueError(msg)

        # 实现：支持逗号与换行分隔的 URL 输入。
        urls = [url.strip() for url in self.urls.replace("\n", ",").split(",") if url.strip()]
        if not urls:
            msg = "No valid URLs provided"
            raise ValueError(msg)

        # 注意：prompt 为空无法指导抽取。
        if not self.prompt:
            msg = "Prompt is required"
            raise ValueError(msg)

        # 实现：去除首尾空白，避免无效提示。
        prompt_text = self.prompt.strip()

        # 注意：未提到 schema 时补充“全面抽取”提示，提升覆盖度。
        enhanced_prompt = prompt_text
        if "schema" not in prompt_text.lower():
            enhanced_prompt = f"{prompt_text}. Please extract all instances in a comprehensive, structured format."

        params = {
            "prompt": enhanced_prompt,
            "enableWebSearch": self.enable_web_search,
            # 注意：可选参数，基础抽取不依赖。
            "ignoreSitemap": self.ignore_sitemap,
            "includeSubdomains": self.include_subdomains,
            "showSources": self.show_sources,
            "timeout": 300,
        }

        # 注意：仅在 schema 合法时写入参数，避免 API 失败。
        if self.schema:
            try:
                if isinstance(self.schema, dict) and "type" in self.schema:
                    params["schema"] = self.schema
                elif hasattr(self.schema, "dict") and "type" in self.schema.dict():
                    params["schema"] = self.schema.dict()
                else:
                    # 注意：schema 无效时跳过，保持基础抽取可用。
                    pass
            except Exception as e:  # noqa: BLE001
                logger.error(f"Invalid schema: {e!s}")

        try:
            app = FirecrawlApp(api_key=self.api_key)
            extract_result = app.extract(urls, params=params)
            return Data(data=extract_result)
        except Exception as e:
            msg = f"Error during extraction: {e!s}"
            raise ValueError(msg) from e
