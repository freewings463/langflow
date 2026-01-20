"""模块名称：YouTube 频道信息组件

本模块提供 YouTube 频道信息与统计数据的获取能力，输出为 DataFrame。
使用场景：根据频道 URL/ID 获取频道概览、统计信息与可选播放列表。
主要功能包括：
- 解析频道 URL 并解析为频道 ID
- 调用 YouTube Data API 获取频道详情
- 可选附加统计、品牌信息与播放列表

关键组件：
- YouTubeChannelComponent：频道信息组件入口

设计背景：统一对 YouTube 频道数据的结构化输出，便于后续分析
注意事项：播放列表默认最多返回 10 条
"""

from typing import Any
from urllib.error import HTTPError

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, MessageTextInput, SecretStrInput
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class YouTubeChannelComponent(Component):
    """YouTube 频道信息组件。

    契约：输入频道 URL/ID 与 API Key，输出包含频道信息的 DataFrame
    关键路径：1) 解析频道 ID 2) 拉取频道详情 3) 组装 DataFrame/播放列表
    副作用：调用 YouTube Data API，消耗配额
    异常流：API 异常返回含 `error` 列的 DataFrame
    决策：限制播放列表最多 10 条；问题：频道列表可能过大；方案：MAX_PLAYLIST_RESULTS=10；
    代价：结果不完整；重评：当需要分页或全量导出时
    """

    display_name: str = "YouTube Channel"
    description: str = "Retrieves detailed information and statistics about YouTube channels as a DataFrame."
    icon: str = "YouTube"

    # 常量
    CHANNEL_ID_LENGTH = 24
    QUOTA_EXCEEDED_STATUS = 403
    NOT_FOUND_STATUS = 404
    MAX_PLAYLIST_RESULTS = 10

    inputs = [
        MessageTextInput(
            name="channel_url",
            display_name="Channel URL or ID",
            info="The URL or ID of the YouTube channel.",
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
            info="Include channel statistics (views, subscribers, videos).",
        ),
        BoolInput(
            name="include_branding",
            display_name="Include Branding",
            value=True,
            info="Include channel branding settings (banner, thumbnails).",
            advanced=True,
        ),
        BoolInput(
            name="include_playlists",
            display_name="Include Playlists",
            value=False,
            info="Include channel's public playlists.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="channel_df", display_name="Channel Info", method="get_channel_info"),
    ]

    def _extract_channel_id(self, channel_url: str) -> str:
        """从多种频道 URL 中提取频道 ID。

        契约：支持 `channel`/`c`/`user`/`@handle` 等格式；无法识别则返回原值
        失败语义：不匹配模式时直接回退原始输入，可能触发后续 API 报错
        """
        import re

        if channel_url.startswith("UC") and len(channel_url) == self.CHANNEL_ID_LENGTH:
            return channel_url

        patterns = {
            "custom_url": r"youtube\.com\/c\/([^\/\n?]+)",
            "channel_id": r"youtube\.com\/channel\/([^\/\n?]+)",
            "user": r"youtube\.com\/user\/([^\/\n?]+)",
            "handle": r"youtube\.com\/@([^\/\n?]+)",
        }

        for pattern_type, pattern in patterns.items():
            match = re.search(pattern, channel_url)
            if match:
                if pattern_type == "channel_id":
                    return match.group(1)
                # 注意：非 ID 格式需通过搜索接口解析为 channelId。
                return self._get_channel_id_by_name(match.group(1), pattern_type)

        return channel_url

    def _get_channel_id_by_name(self, channel_name: str, identifier_type: str) -> str:
        """通过频道名/自定义 URL/handle 获取 channelId。

        契约：使用 Search API 查询并取首条结果
        异常流：API 错误抛 `RuntimeError`；未找到抛 `ValueError`
        排障入口：异常消息包含请求错误详情
        """
        youtube = None
        try:
            youtube = build("youtube", "v3", developerKey=self.api_key)

            if identifier_type == "handle":
                # 注意：handle 可能以 @ 开头，需去除以匹配搜索。
                channel_name = channel_name.lstrip("@")

            request = youtube.search().list(part="id", q=channel_name, type="channel", maxResults=1)
            response = request.execute()

            if response["items"]:
                return response["items"][0]["id"]["channelId"]

            error_msg = f"Could not find channel ID for: {channel_name}"
            raise ValueError(error_msg)

        except (HttpError, HTTPError) as e:
            error_msg = f"YouTube API error while getting channel ID: {e!s}"
            raise RuntimeError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error while getting channel ID: {e!s}"
            raise ValueError(error_msg) from e
        finally:
            if youtube:
                youtube.close()

    def _get_channel_playlists(self, youtube: Any, channel_id: str) -> list[dict[str, Any]]:
        """获取频道公开播放列表（最多 MAX_PLAYLIST_RESULTS 条）。"""
        try:
            playlists_request = youtube.playlists().list(
                part="snippet,contentDetails",
                channelId=channel_id,
                maxResults=self.MAX_PLAYLIST_RESULTS,
            )
            playlists_response = playlists_request.execute()
            playlists = []

            for item in playlists_response.get("items", []):
                playlist_data = {
                    "playlist_title": item["snippet"]["title"],
                    "playlist_description": item["snippet"]["description"],
                    "playlist_id": item["id"],
                    "playlist_video_count": item["contentDetails"]["itemCount"],
                    "playlist_published_at": item["snippet"]["publishedAt"],
                    "playlist_thumbnail_url": item["snippet"]["thumbnails"]["default"]["url"],
                }
                playlists.append(playlist_data)

            return playlists
        except (HttpError, HTTPError) as e:
            return [{"error": str(e)}]
        else:
            return playlists

    def get_channel_info(self) -> DataFrame:
        """获取频道信息并返回 DataFrame。

        关键路径（三步）：
        1) 解析频道 ID 并初始化客户端
        2) 拉取频道详情并拼装字段
        3) 可选合并播放列表数据

        异常流：API 异常返回含 `error` 的 DataFrame
        性能瓶颈：包含播放列表时需额外请求
        """
        youtube = None
        try:
            # 注意：频道 ID 解析失败会回退到原始输入，可能导致 API 报错。
            channel_id = self._extract_channel_id(self.channel_url)
            youtube = build("youtube", "v3", developerKey=self.api_key)

            # 注意：按开关拼接字段以控制配额与返回大小。
            parts = ["snippet", "contentDetails"]
            if self.include_statistics:
                parts.append("statistics")
            if self.include_branding:
                parts.append("brandingSettings")

            channel_response = youtube.channels().list(part=",".join(parts), id=channel_id).execute()

            if not channel_response["items"]:
                return DataFrame(pd.DataFrame({"error": ["Channel not found"]}))

            channel_info = channel_response["items"][0]

            # 注意：DataFrame 以单行形式返回频道主数据。
            channel_data = {
                "title": [channel_info["snippet"]["title"]],
                "description": [channel_info["snippet"]["description"]],
                "custom_url": [channel_info["snippet"].get("customUrl", "")],
                "published_at": [channel_info["snippet"]["publishedAt"]],
                "country": [channel_info["snippet"].get("country", "Not specified")],
                "channel_id": [channel_id],
            }

            # 注意：缩略图按 size 拆分为多个列。
            for size, thumb in channel_info["snippet"]["thumbnails"].items():
                channel_data[f"thumbnail_{size}"] = [thumb["url"]]

            # 注意：统计字段为数值型，避免字符串影响下游统计。
            if self.include_statistics:
                stats = channel_info["statistics"]
                channel_data.update(
                    {
                        "view_count": [int(stats.get("viewCount", 0))],
                        "subscriber_count": [int(stats.get("subscriberCount", 0))],
                        "hidden_subscriber_count": [stats.get("hiddenSubscriberCount", False)],
                        "video_count": [int(stats.get("videoCount", 0))],
                    }
                )

            # 注意：品牌信息属于可选字段，缺失时返回空字符串。
            if self.include_branding:
                branding = channel_info.get("brandingSettings", {})
                channel_data.update(
                    {
                        "brand_title": [branding.get("channel", {}).get("title", "")],
                        "brand_description": [branding.get("channel", {}).get("description", "")],
                        "brand_keywords": [branding.get("channel", {}).get("keywords", "")],
                        "brand_banner_url": [branding.get("image", {}).get("bannerExternalUrl", "")],
                    }
                )

            # 注意：播放列表会扩展为多行，同一频道数据会被复制。
            channel_df = pd.DataFrame(channel_data)

            if self.include_playlists:
                playlists = self._get_channel_playlists(youtube, channel_id)
                if playlists and "error" not in playlists[0]:
                    playlists_df = pd.DataFrame(playlists)
                    channel_df = pd.concat([channel_df] * len(playlists_df), ignore_index=True)
                    for column in playlists_df.columns:
                        channel_df[column] = playlists_df[column].to_numpy()

            return DataFrame(channel_df)

        except (HttpError, HTTPError) as e:
            return DataFrame(pd.DataFrame({"error": [str(e)]}))
        finally:
            if youtube:
                youtube.close()
