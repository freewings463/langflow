"""模块名称：YouTube 评论组件

本模块提供 YouTube 视频评论的获取与展开能力，输出为 DataFrame。
使用场景：抓取评论/回复并用于分析或下游处理。
主要功能包括：
- 解析视频 ID 并分页拉取评论线程
- 可选展开回复与统计指标
- 输出规范化 DataFrame

关键组件：
- YouTubeCommentsComponent：评论获取组件入口

设计背景：统一评论数据结构，便于统计与过滤
注意事项：单次请求最多返回 100 条，超过会分页
"""

from contextlib import contextmanager

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class YouTubeCommentsComponent(Component):
    """YouTube 评论组件。

    契约：输入视频 URL 与 API Key，输出评论 DataFrame
    关键路径：1) 解析视频 ID 2) 分页拉取线程 3) 处理回复与指标
    副作用：调用 YouTube Data API，消耗配额
    异常流：API 异常返回含 `error` 的 DataFrame
    决策：单次请求最多 100 条；问题：API 限制与配额压力；方案：API_MAX_RESULTS=100；
    代价：需要分页循环；重评：当 API 上限变化或引入批量接口时
    """

    display_name: str = "YouTube Comments"
    description: str = "Retrieves and analyzes comments from YouTube videos."
    icon: str = "YouTube"

    # 常量
    COMMENTS_DISABLED_STATUS = 403
    NOT_FOUND_STATUS = 404
    API_MAX_RESULTS = 100

    inputs = [
        MessageTextInput(
            name="video_url",
            display_name="Video URL",
            info="The URL of the YouTube video to get comments from.",
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
            value=20,
            info="The maximum number of comments to return.",
        ),
        DropdownInput(
            name="sort_by",
            display_name="Sort By",
            options=["time", "relevance"],
            value="relevance",
            info="Sort comments by time or relevance.",
        ),
        BoolInput(
            name="include_replies",
            display_name="Include Replies",
            value=False,
            info="Whether to include replies to comments.",
            advanced=True,
        ),
        BoolInput(
            name="include_metrics",
            display_name="Include Metrics",
            value=True,
            info="Include metrics like like count and reply count.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="comments", display_name="Comments", method="get_video_comments"),
    ]

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

    def _process_reply(self, reply: dict, parent_id: str, *, include_metrics: bool = True) -> dict:
        """处理单条回复评论。"""
        reply_snippet = reply["snippet"]
        reply_data = {
            "comment_id": reply["id"],
            "parent_comment_id": parent_id,
            "author": reply_snippet["authorDisplayName"],
            "text": reply_snippet["textDisplay"],
            "published_at": reply_snippet["publishedAt"],
            "is_reply": True,
        }
        if include_metrics:
            reply_data["like_count"] = reply_snippet["likeCount"]
            # 注意：回复不再有子回复，计数固定为 0。
            reply_data["reply_count"] = 0

        return reply_data

    def _process_comment(
        self, item: dict, *, include_metrics: bool = True, include_replies: bool = False
    ) -> list[dict]:
        """处理单条评论线程（含可选回复）。"""
        comment = item["snippet"]["topLevelComment"]["snippet"]
        comment_id = item["snippet"]["topLevelComment"]["id"]

        # 注意：顶层评论的 parent_comment_id 为空字符串。
        processed_comments = [
            {
                "comment_id": comment_id,
                "parent_comment_id": "",
                "author": comment["authorDisplayName"],
                "author_channel_url": comment.get("authorChannelUrl", ""),
                "text": comment["textDisplay"],
                "published_at": comment["publishedAt"],
                "updated_at": comment["updatedAt"],
                "is_reply": False,
            }
        ]

        if include_metrics:
            processed_comments[0].update(
                {
                    "like_count": comment["likeCount"],
                    "reply_count": item["snippet"]["totalReplyCount"],
                }
            )

        if include_replies and item["snippet"]["totalReplyCount"] > 0 and "replies" in item:
            for reply in item["replies"]["comments"]:
                reply_data = self._process_reply(reply, parent_id=comment_id, include_metrics=include_metrics)
                processed_comments.append(reply_data)

        return processed_comments

    @contextmanager
    def youtube_client(self):
        """YouTube API 客户端上下文管理器。"""
        client = build("youtube", "v3", developerKey=self.api_key)
        try:
            yield client
        finally:
            client.close()

    def get_video_comments(self) -> DataFrame:
        """拉取视频评论并返回 DataFrame。

        关键路径（三步）：
        1) 解析视频 ID
        2) 分页获取评论线程
        3) 处理回复与指标并组装 DataFrame

        异常流：API 异常返回含 `error` 的 DataFrame
        性能瓶颈：包含回复时会放大结果量
        """
        try:
            video_id = self._extract_video_id(self.video_url)

            with self.youtube_client() as youtube:
                comments_data = []
                results_count = 0
                request = youtube.commentThreads().list(
                    part="snippet,replies",
                    videoId=video_id,
                    maxResults=min(self.API_MAX_RESULTS, self.max_results),
                    order=self.sort_by,
                    textFormat="plainText",
                )

                while request and results_count < self.max_results:
                    response = request.execute()

                    for item in response.get("items", []):
                        if results_count >= self.max_results:
                            break

                        comments = self._process_comment(
                            item, include_metrics=self.include_metrics, include_replies=self.include_replies
                        )
                        comments_data.extend(comments)
                        results_count += 1

                    # 注意：存在 nextPageToken 且仍需数据时继续翻页。
                    if "nextPageToken" in response and results_count < self.max_results:
                        request = youtube.commentThreads().list(
                            part="snippet,replies",
                            videoId=video_id,
                            maxResults=min(self.API_MAX_RESULTS, self.max_results - results_count),
                            order=self.sort_by,
                            textFormat="plainText",
                            pageToken=response["nextPageToken"],
                        )
                    else:
                        request = None

                # 注意：组装 DataFrame 以便下游处理。
                comments_df = pd.DataFrame(comments_data)

                # 注意：附加视频信息便于溯源。
                comments_df["video_id"] = video_id
                comments_df["video_url"] = self.video_url

                # 注意：统一列顺序便于展示与后续处理。
                column_order = [
                    "video_id",
                    "video_url",
                    "comment_id",
                    "parent_comment_id",
                    "is_reply",
                    "author",
                    "author_channel_url",
                    "text",
                    "published_at",
                    "updated_at",
                ]

                if self.include_metrics:
                    column_order.extend(["like_count", "reply_count"])

                comments_df = comments_df[column_order]

                return DataFrame(comments_df)

        except HttpError as e:
            error_message = f"YouTube API error: {e!s}"
            if e.resp.status == self.COMMENTS_DISABLED_STATUS:
                error_message = "Comments are disabled for this video or API quota exceeded."
            elif e.resp.status == self.NOT_FOUND_STATUS:
                error_message = "Video not found."

            return DataFrame(pd.DataFrame({"error": [error_message]}))
