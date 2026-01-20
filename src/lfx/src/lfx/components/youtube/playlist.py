"""模块名称：YouTube 播放列表组件

本模块提供播放列表视频链接的批量提取能力。
使用场景：将播放列表展开为视频 URL 列表供后续处理。
主要功能包括：
- 读取播放列表并输出所有视频链接

关键组件：
- YouTubePlaylistComponent：播放列表组件入口

设计背景：简化播放列表到视频列表的转换流程
注意事项：依赖 `pytube`，未安装会在导入时失败
"""

from pytube import Playlist

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MessageTextInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class YouTubePlaylistComponent(Component):
    """YouTube 播放列表组件。

    契约：输入播放列表 URL，输出包含 `video_url` 的 DataFrame
    副作用：调用 YouTube 页面解析，可能受网络/反爬影响
    异常流：`pytube` 异常直接上抛
    """
    display_name = "YouTube Playlist"
    description = "Extracts all video URLs from a YouTube playlist."
    icon = "YouTube"

    inputs = [
        MessageTextInput(
            name="playlist_url",
            display_name="Playlist URL",
            info="URL of the YouTube playlist.",
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Video URLs", name="video_urls", method="extract_video_urls"),
    ]

    def extract_video_urls(self) -> DataFrame:
        """提取播放列表中的视频 URL。"""
        playlist_url = self.playlist_url
        playlist = Playlist(playlist_url)
        video_urls = [video.watch_url for video in playlist.videos]

        return DataFrame([Data(data={"video_url": url}) for url in video_urls])
