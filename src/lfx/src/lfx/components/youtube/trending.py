"""模块名称：YouTube 热门趋势组件

本模块提供 YouTube Trending 视频获取能力，输出为 DataFrame。
使用场景：按地区/分类获取热门视频并附带统计或内容细节。
主要功能包括：
- 拉取热门视频列表并支持地区/分类过滤
- 可选附加统计、内容细节与缩略图

关键组件：
- YouTubeTrendingComponent：热门趋势组件入口

设计背景：统一热门视频数据结构，便于展示与分析
注意事项：默认区域“Global”映射为 US
"""

from contextlib import contextmanager

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output

HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
MAX_API_RESULTS = 50


class YouTubeTrendingComponent(Component):
    """YouTube 热门趋势组件。

    契约：输入 API Key 与筛选条件，输出热门视频 DataFrame
    关键路径：1) 组装请求参数 2) 拉取热门列表 3) 组装 DataFrame
    副作用：调用 YouTube Data API，消耗配额
    异常流：API 异常返回含 `error` 的 DataFrame
    决策：Global 映射为 US；问题：API 不支持全球维度；方案：用 US 作为默认；
    代价：结果偏向 US；重评：当 API 支持全球维度或引入多地区聚合时
    """

    display_name: str = "YouTube Trending"
    description: str = "Retrieves trending videos from YouTube with filtering options."
    icon: str = "YouTube"

    # 注意：地区代码用于 `regionCode` 参数映射，Global 默认映射为 US。
    COUNTRY_CODES = {
        "Global": "US",
        "United States": "US",
        "Brazil": "BR",
        "United Kingdom": "GB",
        "India": "IN",
        "Japan": "JP",
        "South Korea": "KR",
        "Germany": "DE",
        "France": "FR",
        "Canada": "CA",
        "Australia": "AU",
        "Spain": "ES",
        "Italy": "IT",
        "Mexico": "MX",
        "Russia": "RU",
        "Netherlands": "NL",
        "Poland": "PL",
        "Argentina": "AR",
    }

    # 注意：视频分类需映射为 `videoCategoryId`。
    VIDEO_CATEGORIES = {
        "All": "0",
        "Film & Animation": "1",
        "Autos & Vehicles": "2",
        "Music": "10",
        "Pets & Animals": "15",
        "Sports": "17",
        "Travel & Events": "19",
        "Gaming": "20",
        "People & Blogs": "22",
        "Comedy": "23",
        "Entertainment": "24",
        "News & Politics": "25",
        "Education": "27",
        "Science & Technology": "28",
        "Nonprofits & Activism": "29",
    }

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="YouTube API Key",
            info="Your YouTube Data API key.",
            required=True,
        ),
        DropdownInput(
            name="region",
            display_name="Region",
            options=list(COUNTRY_CODES.keys()),
            value="Global",
            info="The region to get trending videos from.",
        ),
        DropdownInput(
            name="category",
            display_name="Category",
            options=list(VIDEO_CATEGORIES.keys()),
            value="All",
            info="The category of videos to retrieve.",
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            value=10,
            info="Maximum number of trending videos to return (1-50).",
        ),
        BoolInput(
            name="include_statistics",
            display_name="Include Statistics",
            value=True,
            info="Include video statistics (views, likes, comments).",
        ),
        BoolInput(
            name="include_content_details",
            display_name="Include Content Details",
            value=True,
            info="Include video duration and quality info.",
            advanced=True,
        ),
        BoolInput(
            name="include_thumbnails",
            display_name="Include Thumbnails",
            value=True,
            info="Include video thumbnail URLs.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="trending_videos", display_name="Trending Videos", method="get_trending_videos"),
    ]

    max_results: int

    def _format_duration(self, duration: str) -> str:
        """将 ISO 8601 时长格式化为可读文本。"""
        import re

        # 注意：去掉前缀 PT 以简化解析。
        duration = duration[2:]

        hours = 0
        minutes = 0
        seconds = 0

        time_dict = {}
        for time_unit in ["H", "M", "S"]:
            match = re.search(r"(\d+)" + time_unit, duration)
            if match:
                time_dict[time_unit] = int(match.group(1))

        if "H" in time_dict:
            hours = time_dict["H"]
        if "M" in time_dict:
            minutes = time_dict["M"]
        if "S" in time_dict:
            seconds = time_dict["S"]

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @contextmanager
    def youtube_client(self):
        """YouTube API 客户端上下文管理器。"""
        client = build("youtube", "v3", developerKey=self.api_key)
        try:
            yield client
        finally:
            client.close()

    def get_trending_videos(self) -> DataFrame:
        """获取热门视频并返回 DataFrame。

        关键路径（三步）：
        1) 校验并裁剪 max_results
        2) 组装请求参数并拉取热门列表
        3) 组装统计/内容细节/缩略图字段

        异常流：API 异常返回含 `error` 的 DataFrame
        性能瓶颈：包含内容细节与缩略图时字段数量增加
        """
        try:
            # 注意：API 最大支持 50 条，超过会被裁剪。
            if not 1 <= self.max_results <= MAX_API_RESULTS:
                self.max_results = min(max(1, self.max_results), MAX_API_RESULTS)

            with self.youtube_client() as youtube:
                region_code = self.COUNTRY_CODES[self.region]

                parts = ["snippet"]
                if self.include_statistics:
                    parts.append("statistics")
                if self.include_content_details:
                    parts.append("contentDetails")

                request_params = {
                    "part": ",".join(parts),
                    "chart": "mostPopular",
                    "regionCode": region_code,
                    "maxResults": self.max_results,
                }

                if self.category != "All":
                    request_params["videoCategoryId"] = self.VIDEO_CATEGORIES[self.category]

                request = youtube.videos().list(**request_params)
                response = request.execute()

                videos_data = []
                for item in response.get("items", []):
                    video_data = {
                        "video_id": item["id"],
                        "title": item["snippet"]["title"],
                        "description": item["snippet"]["description"],
                        "channel_id": item["snippet"]["channelId"],
                        "channel_title": item["snippet"]["channelTitle"],
                        "published_at": item["snippet"]["publishedAt"],
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                        "region": self.region,
                        "category": self.category,
                    }

                    if self.include_thumbnails:
                        for size, thumb in item["snippet"]["thumbnails"].items():
                            video_data[f"thumbnail_{size}_url"] = thumb["url"]
                            video_data[f"thumbnail_{size}_width"] = thumb.get("width", 0)
                            video_data[f"thumbnail_{size}_height"] = thumb.get("height", 0)

                    if self.include_statistics and "statistics" in item:
                        video_data.update(
                            {
                                "view_count": int(item["statistics"].get("viewCount", 0)),
                                "like_count": int(item["statistics"].get("likeCount", 0)),
                                "comment_count": int(item["statistics"].get("commentCount", 0)),
                            }
                        )

                    if self.include_content_details and "contentDetails" in item:
                        content_details = item["contentDetails"]
                        video_data.update(
                            {
                                "duration": self._format_duration(content_details["duration"]),
                                "definition": content_details.get("definition", "hd").upper(),
                                "has_captions": content_details.get("caption", "false") == "true",
                                "licensed_content": content_details.get("licensedContent", False),
                                "projection": content_details.get("projection", "rectangular"),
                            }
                        )

                    videos_data.append(video_data)

                # 注意：组装 DataFrame 以便下游处理。
                videos_df = pd.DataFrame(videos_data)

                # 注意：按固定列顺序组织输出，便于展示与排序。
                column_order = [
                    "video_id",
                    "title",
                    "channel_id",
                    "channel_title",
                    "category",
                    "region",
                    "published_at",
                    "url",
                    "description",
                ]

                if self.include_statistics:
                    column_order.extend(["view_count", "like_count", "comment_count"])

                if self.include_content_details:
                    column_order.extend(["duration", "definition", "has_captions", "licensed_content", "projection"])

                # 注意：缩略图列数量不定，统一追加到末尾。
                if self.include_thumbnails:
                    thumbnail_cols = [col for col in videos_df.columns if col.startswith("thumbnail_")]
                    column_order.extend(sorted(thumbnail_cols))

                # 注意：保留未覆盖的列，避免丢失字段。
                remaining_cols = [col for col in videos_df.columns if col not in column_order]
                videos_df = videos_df[column_order + remaining_cols]

                return DataFrame(videos_df)

        except HttpError as e:
            error_message = f"YouTube API error: {e}"
            if e.resp.status == HTTP_FORBIDDEN:
                error_message = "API quota exceeded or access forbidden."
            elif e.resp.status == HTTP_NOT_FOUND:
                error_message = "Resource not found."

            return DataFrame(pd.DataFrame({"error": [error_message]}))

        except Exception as e:  # noqa: BLE001
            logger.exception("An unexpected error occurred:")
            return DataFrame(pd.DataFrame({"error": [str(e)]}))
