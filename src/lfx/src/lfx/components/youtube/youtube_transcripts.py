"""模块名称：YouTube 字幕转写组件

本模块提供 YouTube 视频字幕的获取与分块能力。
使用场景：获取视频字幕，用于摘要、检索或对话上下文。
主要功能包括：
- 解析视频 ID 并拉取字幕
- 可选翻译字幕并按时间分块
- 输出 DataFrame、Message 或 Data

关键组件：
- YouTubeTranscriptsComponent：字幕组件入口

设计背景：统一字幕输出格式并支持多种下游消费方式
注意事项：视频无字幕或受限会抛出可读错误
"""

import re

import pandas as pd
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, YouTubeTranscriptApi

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DropdownInput, IntInput, MultilineInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.template.field.base import Output


class YouTubeTranscriptsComponent(Component):
    """YouTube 字幕组件。

    契约：输入视频 URL 与分块/翻译配置，输出字幕 DataFrame/Message/Data
    关键路径：1) 解析视频 ID 2) 拉取/翻译字幕 3) 按需分块或合并
    副作用：调用 YouTube Transcript API，可能被限流
    异常流：字幕禁用/未找到抛 `RuntimeError` 或返回错误信息
    决策：默认优先英文字幕，缺失时回退自动生成；问题：多语言与可用性不确定；
    方案：find_transcript 优先 en，失败则 find_generated_transcript；代价：非英文视频可能丢失信息；
    重评：当可配置首选语言或提供语言检测时
    """

    display_name: str = "YouTube Transcripts"
    description: str = "Extracts spoken content from YouTube videos with multiple output options."
    icon: str = "YouTube"
    name = "YouTubeTranscripts"

    inputs = [
        MultilineInput(
            name="url",
            display_name="Video URL",
            info="Enter the YouTube video URL to get transcripts from.",
            tool_mode=True,
            required=True,
        ),
        IntInput(
            name="chunk_size_seconds",
            display_name="Chunk Size (seconds)",
            value=60,
            info="The size of each transcript chunk in seconds.",
        ),
        DropdownInput(
            name="translation",
            display_name="Translation Language",
            advanced=True,
            options=["", "en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "hi", "ar", "id"],
            info="Translate the transcripts to the specified language. Leave empty for no translation.",
        ),
    ]

    outputs = [
        Output(name="dataframe", display_name="Chunks", method="get_dataframe_output"),
        Output(name="message", display_name="Transcript", method="get_message_output"),
        Output(name="data_output", display_name="Transcript + Source", method="get_data_output"),
    ]

    def _extract_video_id(self, url: str) -> str:
        """从视频 URL 中提取视频 ID。"""
        patterns = [
            r"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)",
            r"youtube\.com\/watch\?.*?v=([^&\n?#]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        msg = f"Could not extract video ID from URL: {url}"
        raise ValueError(msg)

    def _load_transcripts(self, *, as_chunks: bool = True):
        """加载字幕并根据需要分块。

        关键路径（三步）：
        1) 解析视频 ID
        2) 拉取字幕（可选翻译）
        3) 按需分块或返回连续结果

        异常流：字幕禁用/未找到抛 `RuntimeError`
        """
        try:
            video_id = self._extract_video_id(self.url)
        except ValueError as e:
            msg = f"Invalid YouTube URL: {e}"
            raise ValueError(msg) from e

        try:
            # 注意：使用 v1+ API 需要先创建实例。
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)

            # 注意：未指定翻译时优先英文字幕，失败则回退自动生成字幕。
            if self.translation:
                transcript = transcript_list.find_transcript(["en"])
                transcript = transcript.translate(self.translation)
            else:
                try:
                    transcript = transcript_list.find_transcript(["en"])
                except NoTranscriptFound:
                    transcript = transcript_list.find_generated_transcript(["en"])

            transcript_data = api.fetch(transcript.video_id, [transcript.language_code])

        except (TranscriptsDisabled, NoTranscriptFound) as e:
            error_type = type(e).__name__
            msg = (
                f"Could not retrieve transcripts for video '{video_id}'. "
                "Possible reasons:\n"
                "1. This video does not have captions/transcripts enabled\n"
                "2. The video is private, restricted, or deleted\n"
                f"\nTechnical error ({error_type}): {e}"
            )
            raise RuntimeError(msg) from e
        except Exception as e:
            error_type = type(e).__name__
            msg = (
                f"Could not retrieve transcripts for video '{video_id}'. "
                "Possible reasons:\n"
                "1. This video does not have captions/transcripts enabled\n"
                "2. The video is private, restricted, or deleted\n"
                "3. YouTube is blocking automated requests\n"
                f"\nTechnical error ({error_type}): {e}"
            )
            raise RuntimeError(msg) from e

        if as_chunks:
            return self._chunk_transcript(transcript_data)
        return transcript_data

    def _chunk_transcript(self, transcript_data):
        """按时间窗口将字幕分块。"""
        chunks = []
        current_chunk = []
        chunk_start = 0

        for segment in transcript_data:
            # 注意：兼容旧版 dict 与新版对象格式。
            segment_start = segment.start if hasattr(segment, "start") else segment["start"]

            if segment_start - chunk_start >= self.chunk_size_seconds and current_chunk:
                chunk_text = " ".join(s.text if hasattr(s, "text") else s["text"] for s in current_chunk)
                chunks.append({"start": chunk_start, "text": chunk_text})
                current_chunk = []
                chunk_start = segment_start

            current_chunk.append(segment)

        if current_chunk:
            chunk_text = " ".join(s.text if hasattr(s, "text") else s["text"] for s in current_chunk)
            chunks.append({"start": chunk_start, "text": chunk_text})

        return chunks

    def get_dataframe_output(self) -> DataFrame:
        """输出按时间分块的字幕 DataFrame。"""
        try:
            chunks = self._load_transcripts(as_chunks=True)

            data = []
            for chunk in chunks:
                start_seconds = int(chunk["start"])
                start_minutes = start_seconds // 60
                start_seconds_remainder = start_seconds % 60
                timestamp = f"{start_minutes:02d}:{start_seconds_remainder:02d}"
                data.append({"timestamp": timestamp, "text": chunk["text"]})

            return DataFrame(pd.DataFrame(data))

        except (TranscriptsDisabled, NoTranscriptFound, RuntimeError, ValueError) as exc:
            error_msg = f"Failed to get YouTube transcripts: {exc!s}"
            return DataFrame(pd.DataFrame({"error": [error_msg]}))

    def get_message_output(self) -> Message:
        """输出连续文本字幕 Message。"""
        try:
            transcript_data = self._load_transcripts(as_chunks=False)
            result = " ".join(
                segment.text if hasattr(segment, "text") else segment["text"] for segment in transcript_data
            )
            return Message(text=result)

        except (TranscriptsDisabled, NoTranscriptFound, RuntimeError, ValueError) as exc:
            error_msg = f"Failed to get YouTube transcripts: {exc!s}"
            return Message(text=error_msg)

    def get_data_output(self) -> Data:
        """输出结构化 Data（字幕+元信息）。

        契约：返回包含 `transcript`/`video_url`/`error` 的 Data
        失败语义：异常时将错误信息写入 `error`
        """
        default_data = {"transcript": "", "video_url": self.url, "error": None}

        try:
            transcript_data = self._load_transcripts(as_chunks=False)
            if not transcript_data:
                default_data["error"] = "No transcripts found."
                return Data(data=default_data)

            full_transcript = " ".join(
                segment.text if hasattr(segment, "text") else segment["text"] for segment in transcript_data
            )
            return Data(data={"transcript": full_transcript, "video_url": self.url})

        except (TranscriptsDisabled, NoTranscriptFound, RuntimeError, ValueError) as exc:
            default_data["error"] = str(exc)
            return Data(data=default_data)
