"""
模块名称：`SearXNG` 搜索工具组件

本模块通过 SearXNG 实例提供搜索能力，并支持动态拉取分类与语言配置。
主要功能包括：
- 读取 SearXNG 配置以刷新分类/语言选项
- 构建搜索工具并执行查询
- 控制返回结果数量

关键组件：
- `SearXNGToolComponent.update_build_config`：拉取配置并更新选项
- `SearXNGToolComponent.build_tool`：构建搜索工具

设计背景：支持自部署搜索引擎并与前端配置联动。
注意事项：依赖 SearXNG 服务可用性，超时或格式变化会导致失败。
"""

import json
from collections.abc import Sequence
from typing import Any

import requests
from langchain.agents import Tool
from langchain_core.tools import StructuredTool
from pydantic.v1 import Field, create_model

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.inputs.inputs import DropdownInput, IntInput, MessageTextInput, MultiselectInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict


class SearXNGToolComponent(LCToolComponent):
    """SearXNG 搜索工具组件。

    契约：输入查询与分类，返回限制数量的结果列表。
    决策：启动时从 `/config` 拉取分类与语言配置。
    问题：前端可选项需与服务端配置一致。
    方案：动态请求配置并刷新选项。
    代价：初始化依赖外部服务，失败时需降级。
    重评：当配置稳定或可本地缓存时减少网络请求。
    """
    search_headers: dict = {}
    display_name = "SearXNG Search"
    description = "A component that searches for tools using SearXNG."
    name = "SearXNGTool"
    legacy: bool = True

    inputs = [
        MessageTextInput(
            name="url",
            display_name="URL",
            value="http://localhost",
            required=True,
            refresh_button=True,
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            value=10,
            required=True,
        ),
        MultiselectInput(
            name="categories",
            display_name="Categories",
            options=[],
            value=[],
        ),
        DropdownInput(
            name="language",
            display_name="Language",
            options=[],
        ),
    ]

    outputs = [
        Output(display_name="Tool", name="result_tool", method="build_tool"),
    ]

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None) -> dotdict:
        """根据 SearXNG 配置刷新前端可选项。

        关键路径（三步）：
        1) 请求 `/config` 获取分类与语言
        2) 清理无效已选项
        3) 写回前端选项列表
        异常流：请求失败时写入错误选项并记录状态。
        """
        if field_name is None:
            return build_config

        if field_name != "url":
            return build_config

        try:
            url = f"{field_value}/config"

            response = requests.get(url=url, headers=self.search_headers.copy(), timeout=10)
            data = None
            if response.headers.get("Content-Encoding") == "zstd":
                data = json.loads(response.content)
            else:
                data = response.json()
            build_config["categories"]["options"] = data["categories"].copy()
            for selected_category in build_config["categories"]["value"]:
                if selected_category not in build_config["categories"]["options"]:
                    build_config["categories"]["value"].remove(selected_category)
            languages = list(data["locales"])
            build_config["language"]["options"] = languages.copy()
        except Exception as e:  # noqa: BLE001
            self.status = f"Failed to extract names: {e}"
            logger.debug(self.status, exc_info=True)
            build_config["categories"]["options"] = ["Failed to parse", str(e)]
        return build_config

    def build_tool(self) -> Tool:
        """构建 SearXNG 搜索工具实例。

        关键路径（三步）：
        1) 定义搜索类并注入配置
        2) 生成参数 schema
        3) 构建结构化工具
        """
        class SearxSearch:
            _url: str = ""
            _categories: list[str] = []
            _language: str = ""
            _headers: dict = {}
            _max_results: int = 10

            @staticmethod
            def search(query: str, categories: Sequence[str] = ()) -> list:
                """执行搜索并返回结果列表。"""
                if not SearxSearch._categories and not categories:
                    msg = "No categories provided."
                    raise ValueError(msg)
                all_categories = SearxSearch._categories + list(set(categories) - set(SearxSearch._categories))
                try:
                    url = f"{SearxSearch._url}/"
                    headers = SearxSearch._headers.copy()
                    response = requests.get(
                        url=url,
                        headers=headers,
                        params={
                            "q": query,
                            "categories": ",".join(all_categories),
                            "language": SearxSearch._language,
                            "format": "json",
                        },
                        timeout=10,
                    ).json()

                    num_results = min(SearxSearch._max_results, len(response["results"]))
                    return [response["results"][i] for i in range(num_results)]
                except Exception as e:  # noqa: BLE001
                    logger.debug("Error running SearXNG Search", exc_info=True)
                    return [f"Failed to search: {e}"]

        SearxSearch._url = self.url
        SearxSearch._categories = self.categories.copy()
        SearxSearch._language = self.language
        SearxSearch._headers = self.search_headers.copy()
        SearxSearch._max_results = self.max_results

        globals_ = globals()
        local = {}
        local["SearxSearch"] = SearxSearch
        globals_.update(local)

        schema_fields = {
            "query": (str, Field(..., description="The query to search for.")),
            "categories": (
                list[str],
                Field(default=[], description="The categories to search in."),
            ),
        }

        searx_search_schema = create_model("SearxSearchSchema", **schema_fields)

        return StructuredTool.from_function(
            func=local["SearxSearch"].search,
            args_schema=searx_search_schema,
            name="searxng_search_tool",
            description="A tool that searches for tools using SearXNG.\nThe available categories are: "
            + ", ".join(self.categories),
        )
