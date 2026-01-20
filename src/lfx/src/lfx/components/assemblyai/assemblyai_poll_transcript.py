"""
模块名称：assemblyai_poll_transcript

本模块提供转写任务轮询组件，用于获取转写完成后的结果。
主要功能包括：
- 轮询 AssemblyAI 转写状态
- 规范化并输出转写结果数据

关键组件：
- `AssemblyAITranscriptionJobPoller`：转写轮询组件

设计背景：转写任务为异步，需要轮询获取结果
使用场景：在流程中等待转写完成并消费结果
注意事项：轮询间隔受 `polling_interval` 控制
"""

import assemblyai as aai

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import DataInput, FloatInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class AssemblyAITranscriptionJobPoller(Component):
    """AssemblyAI 转写轮询组件。

    契约：需要 `api_key` 与转写任务 `transcript_id`。
    副作用：设置全局 `aai.settings` 并发起轮询请求。
    失败语义：异常时返回带 `error` 的 `Data`。
    排障入口：日志 `logger.debug` 与 `status`。
    """
    display_name = "AssemblyAI Poll Transcript"
    description = "Poll for the status of a transcription job using AssemblyAI"
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
            name="transcript_id",
            display_name="Transcript ID",
            info="The ID of the transcription job to poll",
            required=True,
        ),
        FloatInput(
            name="polling_interval",
            display_name="Polling Interval",
            value=3.0,
            info="The polling interval in seconds",
            advanced=True,
            range_spec=RangeSpec(min=3, max=30),
        ),
    ]

    outputs = [
        Output(display_name="Transcription Result", name="transcription_result", method="poll_transcription_job"),
    ]

    def poll_transcription_job(self) -> Data:
        """轮询转写状态并返回结果。

        契约：`transcript_id.data` 中需含 `transcript_id`。
        副作用：设置 `aai.settings.polling_interval` 并执行网络请求。
        失败语义：上游错误直接透传；异常时返回 `error`。
        关键路径（三步）：1) 校验输入 2) 拉取转写 3) 规范化输出。
        决策：将 `text/utterances/id` 置于输出前部。
        问题：原始响应字段顺序不稳定，前端消费不友好。
        方案：提取关键字段并优先合并回字典。
        代价：新增一次字典重排操作。
        重评：当下游不依赖字段顺序时。
        """
        aai.settings.api_key = self.api_key
        aai.settings.polling_interval = self.polling_interval

        # 注意：上游已失败则直接返回错误。
        if self.transcript_id.data.get("error"):
            self.status = self.transcript_id.data["error"]
            return self.transcript_id

        try:
            transcript = aai.Transcript.get_by_id(self.transcript_id.data["transcript_id"])
        except Exception as e:  # noqa: BLE001
            error = f"Getting transcription failed: {e}"
            logger.debug(error, exc_info=True)
            self.status = error
            return Data(data={"error": error})

        if transcript.status == aai.TranscriptStatus.completed:
            json_response = transcript.json_response
            text = json_response.pop("text", None)
            utterances = json_response.pop("utterances", None)
            transcript_id = json_response.pop("id", None)
            sorted_data = {"text": text, "utterances": utterances, "id": transcript_id}
            sorted_data.update(json_response)
            data = Data(data=sorted_data)
            self.status = data
            return data
        self.status = transcript.error
        return Data(data={"error": transcript.error})
