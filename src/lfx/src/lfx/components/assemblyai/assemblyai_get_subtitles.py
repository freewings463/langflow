"""
模块名称：assemblyai_get_subtitles

本模块提供 AssemblyAI 字幕导出组件，实现 SRT/VTT 导出能力。
主要功能包括：
- 根据转写结果 ID 拉取字幕
- 输出字幕文本与格式信息

关键组件：
- `AssemblyAIGetSubtitles`：字幕导出组件

设计背景：转写完成后需要生成字幕文件以供发布
使用场景：从转写结果导出 SRT/VTT
注意事项：仅支持已完成的转写任务
"""

import assemblyai as aai

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DropdownInput, IntInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class AssemblyAIGetSubtitles(Component):
    """AssemblyAI 字幕导出组件。

    契约：需要 `api_key` 与有效的转写结果 `id`。
    副作用：调用 AssemblyAI API 并可能写 `status`。
    失败语义：上游错误或 API 异常会返回带 `error` 的 `Data`。
    排障入口：日志 `logger.debug` + `status` 错误信息。
    """

    display_name = "AssemblyAI Get Subtitles"
    description = "Export your transcript in SRT or VTT format for subtitles and closed captions"
    documentation = "https://www.assemblyai.com/docs"
    icon = "AssemblyAI"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Assembly API Key",
            info="Your AssemblyAI API key. You can get one from https://www.assemblyai.com/",
            required=True,
        ),
        DataInput(
            name="transcription_result",
            display_name="Transcription Result",
            info="The transcription result from AssemblyAI",
            required=True,
        ),
        DropdownInput(
            name="subtitle_format",
            display_name="Subtitle Format",
            options=["srt", "vtt"],
            value="srt",
            info="The format of the captions (SRT or VTT)",
        ),
        IntInput(
            name="chars_per_caption",
            display_name="Characters per Caption",
            info="The maximum number of characters per caption (0 for no limit)",
            value=0,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Subtitles", name="subtitles", method="get_subtitles"),
    ]

    def get_subtitles(self) -> Data:
        """拉取字幕并返回结果。

        契约：`transcription_result` 需包含 `id`，字幕格式为 `srt`/`vtt`。
        副作用：设置 `aai.settings.api_key` 并进行网络请求。
        失败语义：上游错误直接透传；异常捕获后返回 `error`。
        关键路径（三步）：1) 读取转写 ID 2) 拉取转写状态 3) 导出字幕。
        决策：仅在状态完成时导出字幕。
        问题：未完成状态导出会返回错误或空结果。
        方案：检查状态，不满足则返回错误。
        代价：调用方需先确保转写完成。
        重评：当 API 支持预完成字幕或异步导出时。
        """
        aai.settings.api_key = self.api_key

        # 注意：上一步已失败则直接透传错误。
        if self.transcription_result.data.get("error"):
            self.status = self.transcription_result.data["error"]
            return self.transcription_result

        try:
            transcript_id = self.transcription_result.data["id"]
            transcript = aai.Transcript.get_by_id(transcript_id)
        except Exception as e:  # noqa: BLE001
            error = f"Getting transcription failed: {e}"
            logger.debug(error, exc_info=True)
            self.status = error
            return Data(data={"error": error})

        if transcript.status == aai.TranscriptStatus.completed:
            subtitles = None
            chars_per_caption = self.chars_per_caption if self.chars_per_caption > 0 else None
            if self.subtitle_format == "srt":
                subtitles = transcript.export_subtitles_srt(chars_per_caption)
            else:
                subtitles = transcript.export_subtitles_vtt(chars_per_caption)

            result = Data(
                subtitles=subtitles,
                format=self.subtitle_format,
                transcript_id=transcript_id,
                chars_per_caption=chars_per_caption,
            )

            self.status = result
            return result
        self.status = transcript.error
        return Data(data={"error": transcript.error})
