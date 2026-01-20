"""
模块名称：TwelveLabs Pegasus 视频索引

本模块调用 TwelveLabs Pegasus API 对视频进行索引，并将 `video_id` 写入元数据。
主要功能包括：
- 获取或创建索引（支持 `index_id` 或 `index_name`）
- 上传视频并轮询任务状态
- 将 `video_id`/`index_id`/`index_name` 回填到 Data

关键组件：
- `PegasusIndexVideo`
- `_get_or_create_index`
- `_wait_for_task_completion`

设计背景：索引流程包含上传与异步任务，需要统一的状态与错误语义。
注意事项：任务轮询具有超时与重试限制，失败会保留状态信息。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential
from twelvelabs import TwelveLabs

from lfx.custom import Component
from lfx.inputs import DataInput, DropdownInput, SecretStrInput, StrInput
from lfx.io import Output
from lfx.schema import Data


class TwelveLabsError(Exception):
    """TwelveLabs 相关异常基类。"""


class IndexCreationError(TwelveLabsError):
    """索引创建或解析失败。"""


class TaskError(TwelveLabsError):
    """任务执行失败。"""


class TaskTimeoutError(TwelveLabsError):
    """任务等待超时。"""


class PegasusIndexVideo(Component):
    """Pegasus 视频索引组件。

    契约：
    - 输入：视频路径 Data、API Key、索引标识与模型名
    - 输出：包含 `video_id` 的 `Data` 列表
    - 副作用：调用 TwelveLabs API 上传视频并创建索引
    - 失败语义：索引/任务异常抛出 `IndexCreationError` 或 `TaskError`
    """

    display_name = "TwelveLabs Pegasus Index Video"
    description = "Index videos using TwelveLabs and add the video_id to metadata."
    icon = "TwelveLabs"
    name = "TwelveLabsPegasusIndexVideo"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    inputs = [
        DataInput(
            name="videodata",
            display_name="Video Data",
            info="Video Data objects (from VideoFile or SplitVideo)",
            is_list=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key", display_name="TwelveLabs API Key", info="Enter your TwelveLabs API Key.", required=True
        ),
        DropdownInput(
            name="model_name",
            display_name="Model",
            info="Pegasus model to use for indexing",
            options=["pegasus1.2"],
            value="pegasus1.2",
            advanced=False,
        ),
        StrInput(
            name="index_name",
            display_name="Index Name",
            info="Name of the index to use. If the index doesn't exist, it will be created.",
            required=False,
        ),
        StrInput(
            name="index_id",
            display_name="Index ID",
            info="ID of an existing index to use. If provided, index_name will be ignored.",
            required=False,
        ),
    ]

    outputs = [
        Output(
            display_name="Indexed Data", name="indexed_data", method="index_videos", output_types=["Data"], is_list=True
        ),
    ]

    def _get_or_create_index(self, client: TwelveLabs) -> tuple[str, str]:
        """获取或创建索引，返回 (index_id, index_name)。"""
        # 注意：优先使用 `index_id`，失败时回退到 `index_name`
        if hasattr(self, "index_id") and self.index_id:
            try:
                index = client.index.retrieve(id=self.index_id)
            except (ValueError, KeyError) as e:
                if not hasattr(self, "index_name") or not self.index_name:
                    error_msg = "Invalid index ID provided and no index name specified for fallback"
                    raise IndexCreationError(error_msg) from e
            else:
                return self.index_id, index.name

        # 注意：按名称查找，未命中则创建
        if hasattr(self, "index_name") and self.index_name:
            try:
                # 拉取索引列表并按名称匹配
                indexes = client.index.list()
                for idx in indexes:
                    if idx.name == self.index_name:
                        return idx.id, idx.name

                # 未找到则新建索引
                index = client.index.create(
                    name=self.index_name,
                    models=[
                        {
                            "name": self.model_name if hasattr(self, "model_name") else "pegasus1.2",
                            "options": ["visual", "audio"],
                        }
                    ],
                )
            except (ValueError, KeyError) as e:
                error_msg = f"Error with index name {self.index_name}"
                raise IndexCreationError(error_msg) from e
            else:
                return index.id, index.name

        # 注意：两者都未提供时直接失败
        error_msg = "Either index_name or index_id must be provided"
        raise IndexCreationError(error_msg)

    def on_task_update(self, task: Any, video_path: str) -> None:
        """任务状态更新回调，刷新组件状态文本。"""
        video_name = Path(video_path).name
        status_msg = f"Indexing {video_name}... Status: {task.status}"
        self.status = status_msg

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=5, max=60), reraise=True)
    def _check_task_status(
        self,
        client: TwelveLabs,
        task_id: str,
        video_path: str,
    ) -> Any:
        """单次查询任务状态，并触发状态回调。"""
        task = client.task.retrieve(id=task_id)
        self.on_task_update(task, video_path)
        return task

    def _wait_for_task_completion(
        self, client: TwelveLabs, task_id: str, video_path: str, max_retries: int = 120, sleep_time: int = 10
    ) -> Any:
        """等待任务完成并覆盖超时/错误场景。

        契约：
        - 输入：`task_id` 与 `video_path`
        - 输出：任务对象（状态为 `ready`）
        - 副作用：更新 `self.status`
        - 失败语义：任务失败/超时抛 `TaskError`/`TaskTimeoutError`

        关键路径（三步）：
        1) 轮询任务状态并刷新组件状态
        2) 对 `ready/failed/error` 做分支判断
        3) 触发超时或连续错误阈值时抛异常
        """
        retries = 0
        consecutive_errors = 0
        max_consecutive_errors = 5
        video_name = Path(video_path).name

        while retries < max_retries:
            try:
                self.status = f"Checking task status for {video_name} (attempt {retries + 1})"
                task = self._check_task_status(client, task_id, video_path)

                if task.status == "ready":
                    self.status = f"Indexing for {video_name} completed successfully!"
                    return task
                if task.status == "failed":
                    error_msg = f"Task failed for {video_name}: {getattr(task, 'error', 'Unknown error')}"
                    self.status = error_msg
                    raise TaskError(error_msg)
                if task.status == "error":
                    error_msg = f"Task encountered an error for {video_name}: {getattr(task, 'error', 'Unknown error')}"
                    self.status = error_msg
                    raise TaskError(error_msg)

                time.sleep(sleep_time)
                retries += 1
                elapsed_time = retries * sleep_time
                self.status = f"Indexing {video_name}... {elapsed_time}s elapsed"

            except (ValueError, KeyError) as e:
                consecutive_errors += 1
                error_msg = f"Error checking task status for {video_name}: {e!s}"
                self.status = error_msg

                if consecutive_errors >= max_consecutive_errors:
                    too_many_errors = f"Too many consecutive errors checking task status for {video_name}"
                    raise TaskError(too_many_errors) from e

                time.sleep(sleep_time * (2**consecutive_errors))
                continue

        timeout_msg = f"Timeout waiting for indexing of {video_name} after {max_retries * sleep_time} seconds"
        self.status = timeout_msg
        raise TaskTimeoutError(timeout_msg)

    def _upload_video(self, client: TwelveLabs, video_path: str, index_id: str) -> str:
        """上传单个视频并返回任务 ID。"""
        video_name = Path(video_path).name
        with Path(video_path).open("rb") as video_file:
            self.status = f"Uploading {video_name} to index {index_id}..."
            task = client.task.create(index_id=index_id, file=video_file)
            task_id = task.id
            self.status = f"Upload complete for {video_name}. Task ID: {task_id}"
            return task_id

    def index_videos(self) -> list[Data]:
        """执行视频索引并回填 `video_id` 到元数据。

        契约：
        - 输入：`videodata` 列表与索引配置
        - 输出：包含回填 `video_id` 的 `Data` 列表
        - 副作用：上传视频、创建索引、更新 `self.status`
        - 失败语义：索引/上传/任务异常会跳过对应视频并记录状态

        关键路径（三步）：
        1) 校验输入与索引配置
        2) 上传视频并并行等待任务完成
        3) 将 `video_id/index_id/index_name` 写入 `metadata`

        异常流：上传或轮询失败会跳过该视频并记录状态。
        """
        if not self.videodata:
            self.status = "No video data provided."
            return []

        if not self.api_key:
            error_msg = "TwelveLabs API Key is required"
            raise IndexCreationError(error_msg)

        if not (hasattr(self, "index_name") and self.index_name) and not (hasattr(self, "index_id") and self.index_id):
            error_msg = "Either index_name or index_id must be provided"
            raise IndexCreationError(error_msg)

        client = TwelveLabs(api_key=self.api_key)
        indexed_data_list: list[Data] = []

        # 注意：索引获取失败直接抛出，避免后续上传浪费配额
        try:
            index_id, index_name = self._get_or_create_index(client)
            self.status = f"Using index: {index_name} (ID: {index_id})"
        except IndexCreationError as e:
            self.status = f"Failed to get/create TwelveLabs index: {e!s}"
            raise

        # 注意：先筛选有效视频，避免无效路径触发批量失败
        valid_videos: list[tuple[Data, str]] = []
        for video_data_item in self.videodata:
            if not isinstance(video_data_item, Data):
                self.status = f"Skipping invalid data item: {video_data_item}"
                continue

            video_info = video_data_item.data
            if not isinstance(video_info, dict):
                self.status = f"Skipping item with invalid data structure: {video_info}"
                continue

            video_path = video_info.get("text")
            if not video_path or not isinstance(video_path, str):
                self.status = f"Skipping item with missing or invalid video path: {video_info}"
                continue

            if not Path(video_path).exists():
                self.status = f"Video file not found, skipping: {video_path}"
                continue

            valid_videos.append((video_data_item, video_path))

        if not valid_videos:
            self.status = "No valid videos to process."
            return []

        # 注意：先完成上传，再并行等待任务
        upload_tasks: list[tuple[Data, str, str]] = []  # 注意：元素为 (data_item, video_path, task_id)
        for data_item, video_path in valid_videos:
            try:
                task_id = self._upload_video(client, video_path, index_id)
                upload_tasks.append((data_item, video_path, task_id))
            except (ValueError, KeyError) as e:
                self.status = f"Failed to upload {video_path}: {e!s}"
                continue

        # 注意：使用线程池并行轮询任务状态
        with ThreadPoolExecutor(max_workers=min(10, len(upload_tasks))) as executor:
            futures = []
            for data_item, video_path, task_id in upload_tasks:
                future = executor.submit(self._wait_for_task_completion, client, task_id, video_path)
                futures.append((data_item, video_path, future))

            # 注意：按任务完成顺序写回元数据
            for data_item, video_path, future in futures:
                try:
                    completed_task = future.result()
                    if completed_task.status == "ready":
                        video_id = completed_task.video_id
                        video_name = Path(video_path).name
                        self.status = f"Video {video_name} indexed successfully. Video ID: {video_id}"

                        # 注意：回填索引与视频标识
                        video_info = data_item.data
                        if "metadata" not in video_info:
                            video_info["metadata"] = {}
                        elif not isinstance(video_info["metadata"], dict):
                            self.status = f"Warning: Overwriting non-dict metadata for {video_path}"
                            video_info["metadata"] = {}

                        video_info["metadata"].update(
                            {"video_id": video_id, "index_id": index_id, "index_name": index_name}
                        )

                        updated_data_item = Data(data=video_info)
                        indexed_data_list.append(updated_data_item)
                except (TaskError, TaskTimeoutError) as e:
                    self.status = f"Failed to process {video_path}: {e!s}"

        if not indexed_data_list:
            self.status = "No videos were successfully indexed."
        else:
            self.status = f"Finished indexing {len(indexed_data_list)}/{len(self.videodata)} videos."

        return indexed_data_list
