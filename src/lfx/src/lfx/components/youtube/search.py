"""模块名称：YouTube 搜索组件

本模块提供 YouTube 视频搜索能力，输出为 DataFrame。
使用场景：根据关键词检索视频并获取可选统计信息。
主要功能包括：
- 调用 YouTube Search API 获取视频列表
- 可选追加统计与时长等元数据

关键组件：
- YouTubeSearchComponent：搜索组件入口

设计背景：统一搜索结果结构，便于下游分析
注意事项：开启 `include_metadata` 会触发额外 API 调用
"""

from contextlib import contextmanager

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class YouTubeSearchComponent(Component):
    """YouTube 搜索组件。

    契约：输入查询词与 API Key，输出视频结果 DataFrame
    关键路径：1) Search API 获取视频列表 2) 可选拉取统计详情
    副作用：调用 YouTube Data API，消耗配额
    异常流：API 异常返回含 `error` 的 DataFrame
    决策：`include_metadata` 时为每条结果拉取详情；问题：Search API 不含统计；
    方案：额外调用 videos().list；代价：配额与延迟增加；重评：当 Search API 提供统计字段时
    """

    display_name: str = "YouTube Search"
    description: str = "Searches YouTube videos based on query."
    icon: str = "YouTube"

    inputs = [
        MessageTextInput(
            name="query",
            display_name="Search Query",
            info="The search query to look for on YouTube.",
            tool_mode=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="YouTube API Key",
            info="Your YouTube Data API key.",
            required=True,
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            value=10,
            info="The maximum number of results to return.",
        ),
        DropdownInput(
            name="order",
            display_name="Sort Order",
            options=["relevance", "date", "rating", "title", "viewCount"],
            value="relevance",
            info="Sort order for the search results.",
        ),
        BoolInput(
            name="include_metadata",
            display_name="Include Metadata",
            value=True,
            info="Include video metadata like description and statistics.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="results", display_name="Search Results", method="search_videos"),
    ]

    @contextmanager
    def youtube_client(self):
        """YouTube API 客户端上下文管理器。"""
        client = build("youtube", "v3", developerKey=self.api_key)
        try:
            yield client
        finally:
            client.close()

    def search_videos(self) -> DataFrame:
        """搜索视频并返回 DataFrame。

        关键路径（三步）：
        1) 调用 Search API 获取视频列表
        2) 可选拉取每条视频详情
        3) 组装结果 DataFrame

        异常流：API 异常返回含 `error` 的 DataFrame
        """
        try:
            with self.youtube_client() as youtube:
                search_response = (
                    youtube.search()
                    .list(
                        q=self.query,
                        part="id,snippet",
                        maxResults=self.max_results,
                        order=self.order,
                        type="video",
                    )
                    .execute()
                )

                results = []
                for search_result in search_response.get("items", []):
                    video_id = search_result["id"]["videoId"]
                    snippet = search_result["snippet"]

                    result = {
                        "video_id": video_id,
                        "title": snippet["title"],
                        "description": snippet["description"],
                        "published_at": snippet["publishedAt"],
                        "channel_title": snippet["channelTitle"],
                        "thumbnail_url": snippet["thumbnails"]["default"]["url"],
                    }

                    if self.include_metadata:
                        # 注意：每条视频会触发额外请求，配额与延迟上升。
                        video_response = youtube.videos().list(part="statistics,contentDetails", id=video_id).execute()

                        if video_response.get("items"):
                            video_details = video_response["items"][0]
                            result.update(
                                {
                                    "view_count": int(video_details["statistics"]["viewCount"]),
                                    "like_count": int(video_details["statistics"].get("likeCount", 0)),
                                    "comment_count": int(video_details["statistics"].get("commentCount", 0)),
                                    "duration": video_details["contentDetails"]["duration"],
                                }
                            )

                    results.append(result)

                return DataFrame(pd.DataFrame(results))

        except HttpError as e:
            error_message = f"YouTube API error: {e!s}"
            return DataFrame(pd.DataFrame({"error": [error_message]}))
