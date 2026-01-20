"""
模块名称：assemblyai_start_transcript

本模块提供 AssemblyAI 转写任务创建组件，支持本地文件或 URL。
主要功能包括：
- 构造转写配置并提交任务
- 返回转写任务 ID 供后续轮询

关键组件：
- `AssemblyAITranscriptionJobCreator`：转写任务创建组件

设计背景：音频转写为异步任务，需要先提交再轮询
使用场景：上传音频后创建转写任务
注意事项：`audio_file` 与 `audio_file_url` 至少提供其一
"""

from pathlib import Path

import assemblyai as aai

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, FileInput, MessageTextInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class AssemblyAITranscriptionJobCreator(Component):
    """AssemblyAI 转写任务创建组件。

    契约：必须提供 `api_key`，并提供音频文件或 URL。
    副作用：读取本地文件路径并调用 AssemblyAI API。
    失败语义：输入校验或 API 失败返回带 `error` 的 `Data`。
    排障入口：日志 `Error submitting transcription job` 与 `status`。
    """
    display_name = "AssemblyAI Start Transcript"
    description = "Create a transcription job for an audio file using AssemblyAI with advanced options"
    documentation = "https://www.assemblyai.com/docs"
    icon = "AssemblyAI"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Assembly API Key",
            info="Your AssemblyAI API key. You can get one from https://www.assemblyai.com/",
            required=True,
        ),
        FileInput(
            name="audio_file",
            display_name="Audio File",
            file_types=[
                "3ga",
                "8svx",
                "aac",
                "ac3",
                "aif",
                "aiff",
                "alac",
                "amr",
                "ape",
                "au",
                "dss",
                "flac",
                "flv",
                "m4a",
                "m4b",
                "m4p",
                "m4r",
                "mp3",
                "mpga",
                "ogg",
                "oga",
                "mogg",
                "opus",
                "qcp",
                "tta",
                "voc",
                "wav",
                "wma",
                "wv",
                "webm",
                "mts",
                "m2ts",
                "ts",
                "mov",
                "mp2",
                "mp4",
                "m4p",
                "m4v",
                "mxf",
            ],
            info="The audio file to transcribe",
            required=True,
        ),
        MessageTextInput(
            name="audio_file_url",
            display_name="Audio File URL",
            info="The URL of the audio file to transcribe (Can be used instead of a File)",
            advanced=True,
        ),
        DropdownInput(
            name="speech_model",
            display_name="Speech Model",
            options=[
                "best",
                "nano",
            ],
            value="best",
            info="The speech model to use for the transcription",
            advanced=True,
        ),
        BoolInput(
            name="language_detection",
            display_name="Automatic Language Detection",
            info="Enable automatic language detection",
            advanced=True,
        ),
        MessageTextInput(
            name="language_code",
            display_name="Language",
            info=(
                """
            The language of the audio file. Can be set manually if automatic language detection is disabled.
            See https://www.assemblyai.com/docs/getting-started/supported-languages """
                "for a list of supported language codes."
            ),
            advanced=True,
        ),
        BoolInput(
            name="speaker_labels",
            display_name="Enable Speaker Labels",
            info="Enable speaker diarization",
        ),
        MessageTextInput(
            name="speakers_expected",
            display_name="Expected Number of Speakers",
            info="Set the expected number of speakers (optional, enter a number)",
            advanced=True,
        ),
        BoolInput(
            name="punctuate",
            display_name="Punctuate",
            info="Enable automatic punctuation",
            advanced=True,
            value=True,
        ),
        BoolInput(
            name="format_text",
            display_name="Format Text",
            info="Enable text formatting",
            advanced=True,
            value=True,
        ),
    ]

    outputs = [
        Output(display_name="Transcript ID", name="transcript_id", method="create_transcription_job"),
    ]

    def create_transcription_job(self) -> Data:
        """创建转写任务并返回任务 ID。

        契约：`audio_file` 与 `audio_file_url` 至少提供其一。
        副作用：设置 `aai.settings.api_key` 并提交转写任务。
        失败语义：校验失败或 API 异常时返回 `error`。
        关键路径（三步）：1) 校验输入 2) 构造配置 3) 提交任务。
        决策：当同时提供文件与 URL 时优先使用本地文件。
        问题：二者同时存在会导致数据源不确定。
        方案：忽略 URL 并记录告警。
        代价：调用方需自行保证 URL 未被误忽略。
        重评：当需要支持多源备份策略时。
        """
        aai.settings.api_key = self.api_key

        # 注意：允许为空，否则转换为整数。
        speakers_expected = None
        if self.speakers_expected and self.speakers_expected.strip():
            try:
                speakers_expected = int(self.speakers_expected)
            except ValueError:
                self.status = "Error: Expected Number of Speakers must be a valid integer"
                return Data(data={"error": "Error: Expected Number of Speakers must be a valid integer"})

        language_code = self.language_code or None

        config = aai.TranscriptionConfig(
            speech_model=self.speech_model,
            language_detection=self.language_detection,
            language_code=language_code,
            speaker_labels=self.speaker_labels,
            speakers_expected=speakers_expected,
            punctuate=self.punctuate,
            format_text=self.format_text,
        )

        audio = None
        if self.audio_file:
            if self.audio_file_url:
                logger.warning("Both an audio file an audio URL were specified. The audio URL was ignored.")

            # 注意：本地文件必须存在。
            if not Path(self.audio_file).exists():
                self.status = "Error: Audio file not found"
                return Data(data={"error": "Error: Audio file not found"})
            audio = self.audio_file
        elif self.audio_file_url:
            audio = self.audio_file_url
        else:
            self.status = "Error: Either an audio file or an audio URL must be specified"
            return Data(data={"error": "Error: Either an audio file or an audio URL must be specified"})

        try:
            transcript = aai.Transcriber().submit(audio, config=config)
        except Exception as e:  # noqa: BLE001
            logger.debug("Error submitting transcription job", exc_info=True)
            self.status = f"An error occurred: {e}"
            return Data(data={"error": f"An error occurred: {e}"})

        if transcript.error:
            self.status = transcript.error
            return Data(data={"error": transcript.error})
        result = Data(data={"transcript_id": transcript.id})
        self.status = result
        return result
