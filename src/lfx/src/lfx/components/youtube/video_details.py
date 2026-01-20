"""模块名称：YouTube 视频详情组件

本模块提供 YouTube 视频详情与统计信息的获取能力，输出为 DataFrame。
使用场景：根据视频 URL 拉取标题、描述、统计、内容细节等信息。
主要功能包括：
- 解析视频 ID 并调用 videos API
- 可选附加统计、标签与缩略图

关键组件：
- YouTubeVideoDetailsComponent：视频详情组件入口

设计背景：统一视频详情字段结构，便于分析与展示
注意事项：视频不存在时返回包含 `error` 的 DataFrame
"""

from contextlib import contextmanager

import googleapiclient
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, MessageTextInput, SecretStrInput
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class YouTubeVideoDetailsComponent(Component):
    """YouTube 视频详情组件。

    契约：输入视频 URL 与 API Key，输出视频详情 DataFrame
    关键路径：1) 解析视频 ID 2) 拉取视频详情 3) 组装 DataFrame
    副作用：调用 YouTube Data API，消耗配额
    异常流：API 异常返回含 `error` 的 DataFrame
    """

    display_name: str = "YouTube Video Details"
    description: str = "Retrieves detailed information and statistics about YouTube videos."
    icon: str = "YouTube"

    inputs = [
        MessageTextInput(
            name="video_url",
            display_name="Video URL",
            info="The URL of the YouTube video.",
            tool_mode=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="YouTube API Key",
            info="Your YouTube Data API key.",
            required=True,
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
            info="Include video duration, quality, and age restriction info.",
            advanced=True,
        ),
        BoolInput(
            name="include_tags",
            display_name="Include Tags",
            value=True,
            info="Include video tags and keywords.",
            advanced=True,
        ),
        BoolInput(
            name="include_thumbnails",
            display_name="Include Thumbnails",
            value=True,
            info="Include video thumbnail URLs in different resolutions.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="video_data", display_name="Video Data", method="get_video_details"),
    ]

    API_FORBIDDEN = 403
    VIDEO_NOT_FOUND = 404

    @contextmanager
    def youtube_client(self):
        """YouTube API 客户端上下文管理器。"""
        client = build("youtube", "v3", developerKey=self.api_key)
        try:
            yield client
        finally:
            client.close()

    def _extract_video_id(self, video_url: str) -> str:
        """从视频 URL 中提取视频 ID。"""
        import re

        patterns = [
            r"(?:youtube\.com\/watch\?v=|youtu.be\/|youtube.com\/embed\/)([^&\n?#]+)",
            r"youtube.com\/shorts\/([^&\n?#]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, video_url)
            if match:
                return match.group(1)

        return video_url.strip()

    def _format_duration(self, duration: str) -> str:
        """将 ISO 8601 时长格式化为可读文本。"""
        import re

        hours = 0
        minutes = 0
        seconds = 0

        hours_match = re.search(r"(\d+)H", duration)
        minutes_match = re.search(r"(\d+)M", duration)
        seconds_match = re.search(r"(\d+)S", duration)

        if hours_match:
            hours = int(hours_match.group(1))
        if minutes_match:
            minutes = int(minutes_match.group(1))
        if seconds_match:
            seconds = int(seconds_match.group(1))

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def get_video_details(self) -> DataFrame:
        """获取视频详情并返回 DataFrame。

        关键路径（三步）：
        1) 解析视频 ID
        2) 拉取视频详情并拼装字段
        3) 按配置重排输出列

        异常流：API 异常返回含 `error` 的 DataFrame
        """
        try:
            with self.youtube_client() as youtube:
                video_id = self._extract_video_id(self.video_url)

                parts = ["snippet"]
                if self.include_statistics:
                    parts.append("statistics")
                if self.include_content_details:
                    parts.append("contentDetails")

                video_response = youtube.videos().list(part=",".join(parts), id=video_id).execute()

                if not video_response["items"]:
                    return DataFrame(pd.DataFrame({"error": ["Video not found"]}))

                video_info = video_response["items"][0]
                snippet = video_info["snippet"]

                # 注意：基础字段保持单行结构，便于合并统计与内容细节。
                video_data = {
                    "video_id": [video_id],
                    "url": [f"https://www.youtube.com/watch?v={video_id}"],
                    "title": [snippet["title"]],
                    "description": [snippet["description"]],
                    "published_at": [snippet["publishedAt"]],
                    "channel_id": [snippet["channelId"]],
                    "channel_title": [snippet["channelTitle"]],
                    "category_id": [snippet.get("categoryId", "Unknown")],
                    "live_broadcast_content": [snippet.get("liveBroadcastContent", "none")],
                }

                if self.include_thumbnails:
                    for size, thumb in snippet["thumbnails"].items():
                        video_data[f"thumbnail_{size}_url"] = [thumb["url"]]
                        video_data[f"thumbnail_{size}_width"] = [thumb.get("width", 0)]
                        video_data[f"thumbnail_{size}_height"] = [thumb.get("height", 0)]

                if self.include_tags and "tags" in snippet:
                    video_data["tags"] = [", ".join(snippet["tags"])]
                    video_data["tags_count"] = [len(snippet["tags"])]

                if self.include_statistics and "statistics" in video_info:
                    stats = video_info["statistics"]
                    video_data.update(
                        {
                            "view_count": [int(stats.get("viewCount", 0))],
                            "like_count": [int(stats.get("likeCount", 0))],
                            "favorite_count": [int(stats.get("favoriteCount", 0))],
                            "comment_count": [int(stats.get("commentCount", 0))],
                        }
                    )

                if self.include_content_details and "contentDetails" in video_info:
                    content_details = video_info["contentDetails"]
                    video_data.update(
                        {
                            "duration": [self._format_duration(content_details["duration"])],
                            "dimension": [content_details.get("dimension", "2d")],
                            "definition": [content_details.get("definition", "hd").upper()],
                            "has_captions": [content_details.get("caption", "false") == "true"],
                            "licensed_content": [content_details.get("licensedContent", False)],
                            "projection": [content_details.get("projection", "rectangular")],
                            "has_custom_thumbnails": [content_details.get("hasCustomThumbnail", False)],
                        }
                    )

                    if "contentRating" in content_details:
                        rating_info = content_details["contentRating"]
                        video_data["content_rating"] = [str(rating_info)]

                video_df = pd.DataFrame(video_data)

                basic_cols = [
                    "video_id",
                    "title",
                    "url",
                    "channel_id",
                    "channel_title",
                    "published_at",
                    "category_id",
                    "live_broadcast_content",
                    "description",
                ]

                stat_cols = ["view_count", "like_count", "favorite_count", "comment_count"]

                content_cols = [
                    "duration",
                    "dimension",
                    "definition",
                    "has_captions",
                    "licensed_content",
                    "projection",
                    "has_custom_thumbnails",
                    "content_rating",
                ]

                tag_cols = ["tags", "tags_count"]

                thumb_cols = [col for col in video_df.columns if col.startswith("thumbnail_")]

                ordered_cols = basic_cols.copy()

                if self.include_statistics:
                    ordered_cols.extend([col for col in stat_cols if col in video_df.columns])

                if self.include_content_details:
                    ordered_cols.extend([col for col in content_cols if col in video_df.columns])

                if self.include_tags:
                    ordered_cols.extend([col for col in tag_cols if col in video_df.columns])

                if self.include_thumbnails:
                    ordered_cols.extend(sorted(thumb_cols))

                remaining_cols = [col for col in video_df.columns if col not in ordered_cols]
                ordered_cols.extend(remaining_cols)

                return DataFrame(video_df[ordered_cols])

        except (HttpError, googleapiclient.errors.HttpError) as e:
            error_message = f"YouTube API error: {e!s}"
            if e.resp.status == self.API_FORBIDDEN:
                error_message = "API quota exceeded or access forbidden."
            elif e.resp.status == self.VIDEO_NOT_FOUND:
                error_message = "Video not found."

            return DataFrame(pd.DataFrame({"error": [error_message]}))

        except KeyError as e:
            return DataFrame(pd.DataFrame({"error": [str(e)]}))
