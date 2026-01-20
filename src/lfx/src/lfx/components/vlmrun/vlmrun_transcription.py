"""
模块名称：VLMRun 转写组件

本模块封装 VLM Run 的音视频转写调用流程，支持文件上传与 URL 处理。
主要功能：
- 校验输入并初始化 VLMRun 客户端；
- 触发转写任务并等待结果；
- 统一输出结构化结果。

关键组件：
- VLMRunTranscription：转写组件入口。

设计背景：提供标准化的外部音视频转写能力，便于在流程中复用。
注意事项：依赖 VLMRun SDK 与网络可用性；长任务需合理设置超时。
"""

from pathlib import Path
from urllib.parse import urlparse

from langflow.custom.custom_component.component import Component
from langflow.io import (
    DropdownInput,
    FileInput,
    IntInput,
    MessageTextInput,
    Output,
    SecretStrInput,
)
from langflow.schema.data import Data
from loguru import logger


class VLMRunTranscription(Component):
    """VLM Run 音视频转写组件

    契约：输入 `api_key` 与媒体文件/URL；输出 `Data` 结果结构。
    关键路径：1) 校验输入 2) 调用 VLMRun 生成任务 3) 解析结果并返回。
    决策：使用 VLMRun SDK 执行转写任务
    问题：需要稳定的音视频转写能力
    方案：使用官方 SDK + batch 处理
    代价：依赖外部服务与 SDK 版本
    重评：当需要自建转写服务时
    """
    display_name = "VLM Run Transcription"
    description = "Extract structured data from audio and video using [VLM Run AI](https://app.vlm.run)"
    documentation = "https://docs.vlm.run"
    icon = "VLMRun"
    beta = True

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="VLM Run API Key",
            info="Get your API key from https://app.vlm.run",
            required=True,
        ),
        DropdownInput(
            name="media_type",
            display_name="Media Type",
            options=["audio", "video"],
            value="audio",
            info="Select the type of media to process",
        ),
        FileInput(
            name="media_files",
            display_name="Media Files",
            file_types=[
                "mp3",
                "wav",
                "m4a",
                "flac",
                "ogg",
                "opus",
                "webm",
                "aac",
                "mp4",
                "mov",
                "avi",
                "mkv",
                "flv",
                "wmv",
                "m4v",
            ],
            info="Upload one or more audio/video files",
            required=False,
            is_list=True,
        ),
        MessageTextInput(
            name="media_url",
            display_name="Media URL",
            info="URL to media file (alternative to file upload)",
            required=False,
            advanced=True,
        ),
        IntInput(
            name="timeout_seconds",
            display_name="Timeout (seconds)",
            value=600,
            info="Maximum time to wait for processing completion",
            advanced=True,
        ),
        DropdownInput(
            name="domain",
            display_name="Processing Domain",
            options=["transcription"],
            value="transcription",
            info="Select the processing domain",
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            display_name="Result",
            name="result",
            method="process_media",
        ),
    ]

    def _check_inputs(self) -> str | None:
        """校验输入是否包含文件或 URL

        契约：未提供媒体时返回错误字符串，否则返回 None。
        """
        if not self.media_files and not self.media_url:
            return "Either media files or media URL must be provided"
        return None

    def _import_vlmrun(self):
        """导入并返回 VLMRun 客户端类

        契约：成功返回 VLMRun 类；失败抛 `ImportError`。
        决策：运行时导入 SDK
        问题：SDK 可能未安装
        方案：捕获 ImportError 并提示安装命令
        代价：首次导入有延迟
        重评：当 SDK 作为硬依赖时
        """
        try:
            from vlmrun.client import VLMRun
        except ImportError as e:
            error_msg = "VLM Run SDK not installed. Run: pip install 'vlmrun[all]'"
            raise ImportError(error_msg) from e
        else:
            return VLMRun

    def _generate_media_response(self, client, media_source):
        """触发音视频生成任务

        契约：返回 VLMRun 任务响应对象。
        关键路径：1) 组装 domain 2) 根据媒体类型调用对应接口。
        """
        domain_str = f"{self.media_type}.{self.domain}"

        if self.media_type == "audio":
            if isinstance(media_source, Path):
                return client.audio.generate(file=media_source, domain=domain_str, batch=True)
            return client.audio.generate(url=media_source, domain=domain_str, batch=True)
        # 注意：视频分支使用 video API。
        if isinstance(media_source, Path):
            return client.video.generate(file=media_source, domain=domain_str, batch=True)
        return client.video.generate(url=media_source, domain=domain_str, batch=True)

    def _wait_for_response(self, client, response):
        """等待批处理任务完成

        契约：若响应包含 id，则等待任务完成并返回最终响应。
        关键路径：1) 判断是否为异步响应 2) 调用 wait。
        """
        if hasattr(response, "id"):
            return client.predictions.wait(response.id, timeout=self.timeout_seconds)
        return response

    def _extract_transcription(self, segments: list) -> list[str]:
        """从分段结果中提取转写文本

        契约：返回转写片段列表；根据 media_type 选择字段。
        关键路径：1) 遍历 segments 2) 提取 audio/video 文本。
        """
        transcription_parts = []
        for segment in segments:
            if self.media_type == "audio" and "audio" in segment:
                transcription_parts.append(segment["audio"].get("content", ""))
            elif self.media_type == "video" and "video" in segment:
                transcription_parts.append(segment["video"].get("content", ""))
                # 注意：视频结果中若包含音频文本则追加，避免丢失信息。
                if "audio" in segment:
                    audio_content = segment["audio"].get("content", "")
                    if audio_content and audio_content.strip():
                        transcription_parts.append(f"[Audio: {audio_content}]")
        return transcription_parts

    def _create_result_dict(self, response, transcription_parts: list, source_name: str) -> dict:
        """构建标准化结果结构

        契约：返回统一字段字典，包含元数据与转写文本。
        关键路径：1) 读取响应字段 2) 组装 metadata/usage/status。
        """
        response_data = response.response if hasattr(response, "response") else {}
        result = {
            "prediction_id": response.id if hasattr(response, "id") else None,
            "transcription": " ".join(transcription_parts),
            "full_response": response_data,
            "metadata": {
                "media_type": self.media_type,
                "duration": response_data.get("metadata", {}).get("duration", 0),
            },
            "usage": response.usage if hasattr(response, "usage") else None,
            "status": response.status if hasattr(response, "status") else "completed",
        }

        # 注意：按来源类型区分 URL 与本地文件名。
        parsed_url = urlparse(source_name)
        if parsed_url.scheme in ["http", "https", "s3", "gs", "ftp", "ftps"]:
            result["source"] = source_name
        else:
            result["filename"] = source_name

        return result

    def _process_single_media(self, client, media_source, source_name: str) -> dict:
        """处理单个媒体文件或 URL

        契约：返回标准化结果字典。
        关键路径：1) 生成任务 2) 等待完成 3) 解析并构建结果。
        """
        response = self._generate_media_response(client, media_source)
        response = self._wait_for_response(client, response)
        response_data = response.response if hasattr(response, "response") else {}
        segments = response_data.get("segments", [])
        transcription_parts = self._extract_transcription(segments)
        return self._create_result_dict(response, transcription_parts, source_name)

    def process_media(self) -> Data:
        """处理音视频并返回结构化结果

        契约：返回 `Data`，包含 `results/total_files` 或错误信息。
        关键路径（三步）：
        1) 校验输入并初始化客户端
        2) 处理文件列表或 URL
        3) 汇总结果并返回
        异常流：导入失败/连接失败/解析异常返回错误 Data。
        排障入口：日志 `Error processing media with VLM Run`。
        决策：统一返回结构化结果列表
        问题：多文件处理需要统一输出格式
        方案：输出 `results` + `total_files`
        代价：上游需要解包 results
        重评：当上游支持流式返回时
        """
        # 注意：先校验输入，避免无效调用。
        error_msg = self._check_inputs()
        if error_msg:
            self.status = error_msg
            return Data(data={"error": error_msg})

        try:
            # 实现：按需导入并初始化 VLMRun 客户端。
            vlmrun_class = self._import_vlmrun()
            client = vlmrun_class(api_key=self.api_key)
            all_results = []

            # 注意：支持批量文件处理，逐个更新状态。
            if self.media_files:
                files_to_process = self.media_files if isinstance(self.media_files, list) else [self.media_files]
                for idx, media_file in enumerate(files_to_process):
                    self.status = f"Processing file {idx + 1} of {len(files_to_process)}..."
                    result = self._process_single_media(client, Path(media_file), Path(media_file).name)
                    all_results.append(result)

            # 注意：若提供 URL，则走 URL 单文件处理路径。
            elif self.media_url:
                result = self._process_single_media(client, self.media_url, self.media_url)
                all_results.append(result)

            # 实现：统一输出结构，便于下游消费。
            output_data = {
                "results": all_results,
                "total_files": len(all_results),
            }
            self.status = f"Successfully processed {len(all_results)} file(s)"
            return Data(data=output_data)

        except ImportError as e:
            self.status = str(e)
            return Data(data={"error": str(e)})
        except (ValueError, ConnectionError, TimeoutError) as e:
            logger.opt(exception=True).debug("Error processing media with VLM Run")
            error_msg = f"Processing failed: {e!s}"
            self.status = error_msg
            return Data(data={"error": error_msg})
        except (AttributeError, KeyError, OSError) as e:
            logger.opt(exception=True).debug("Unexpected error processing media with VLM Run")
            error_msg = f"Unexpected error: {e!s}"
            self.status = error_msg
            return Data(data={"error": error_msg})
