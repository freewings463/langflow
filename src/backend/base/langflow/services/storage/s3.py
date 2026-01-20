"""模块名称：`S3` 对象存储服务

模块目的：提供基于 `S3` 的对象存储实现。
主要功能：文件上传、下载、删除、列表与流式读取。
使用场景：配置启用对象存储并需要跨实例共享文件时使用。
关键组件：`S3StorageService`
设计背景：通过 `aioboto3` 异步客户端适配 `S3` 访问。
注意事项：需配置 `bucket`、凭证与区域；`append` 不支持。
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from langflow.logging.logger import logger

from .service import StorageService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langflow.services.session.service import SessionService
    from langflow.services.settings.service import SettingsService


class S3StorageService(StorageService):
    """基于 `S3` 的存储服务实现。"""

    def __init__(self, session_service: SessionService, settings_service: SettingsService) -> None:
        """初始化 `S3` 存储服务。

        关键路径（三步）：
        1) 校验 `bucket`、前缀与标签配置
        2) 确保 `aioboto3` 可用并创建 `Session`
        3) 标记服务就绪并输出初始化日志

        异常流：缺少 `aioboto3` 抛 `ImportError`；缺少 `bucket` 抛 `ValueError`。
        性能瓶颈：首次创建 `Session` 与环境变量解析。
        排障入口：日志关键字 `S3 storage initialized`。
        """
        super().__init__(session_service, settings_service)

        # 校验必要配置
        self.bucket_name = settings_service.settings.object_storage_bucket_name
        if not self.bucket_name:
            msg = "S3 bucket name is required when using S3 storage"
            raise ValueError(msg)

        self.prefix = settings_service.settings.object_storage_prefix or ""
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"

        self.tags = settings_service.settings.object_storage_tags or {}

        try:
            import aioboto3
        except ImportError as exc:
            msg = "aioboto3 is required for S3 storage. Install it with: uv pip install aioboto3"
            raise ImportError(msg) from exc

        # 创建会话，凭证由环境变量提供
        self.session = aioboto3.Session()
        self._client = None

        self.set_ready()
        logger.info(
            f"S3 storage initialized: bucket={self.bucket_name}, prefix={self.prefix}, "
            f"region={os.getenv('AWS_DEFAULT_REGION', 'default')}"
        )

    def build_full_path(self, flow_id: str, file_name: str) -> str:
        """构建 `S3` 对象键。"""
        # 注意：`prefix` 已包含末尾 `/`
        return f"{self.prefix}{flow_id}/{file_name}"

    def parse_file_path(self, full_path: str) -> tuple[str, str]:
        """解析 `S3` 路径并提取 `flow_id` 与文件名。

        关键路径（三步）：
        1) 若包含 `prefix` 则去除
        2) 以最后一个 `/` 拆分路径
        3) 返回 `(flow_id, file_name)`

        异常流：无法拆分时返回 `("", file_name)`。
        性能瓶颈：字符串处理开销。
        排障入口：无日志，需由调用方校验返回值。
        """
        path_without_prefix = full_path
        if self.prefix and full_path.startswith(self.prefix):
            path_without_prefix = full_path[len(self.prefix) :]

        if "/" not in path_without_prefix:
            return "", path_without_prefix

        flow_id, file_name = path_without_prefix.rsplit("/", 1)
        return flow_id, file_name

    def resolve_component_path(self, logical_path: str) -> str:
        """`S3` 模式下保持逻辑路径不变。"""
        return logical_path

    def _get_client(self):
        """获取 `S3` 客户端（通过异步上下文管理器）。"""
        return self.session.client("s3")

    async def save_file(self, flow_id: str, file_name: str, data: bytes, *, append: bool = False) -> None:
        """保存文件到 `S3`。

        关键路径（三步）：
        1) 校验 `append` 模式
        2) 组装 `put_object` 参数并写入
        3) 解析异常并映射为明确错误

        异常流：不支持 `append` 抛 `NotImplementedError`；访问错误映射为权限或不存在异常。
        性能瓶颈：网络上传与 `put_object` 请求。
        排障入口：日志关键字 `Error saving file`。
        """
        if append:
            msg = "Append mode is not supported for S3 storage"
            raise NotImplementedError(msg)

        key = self.build_full_path(flow_id, file_name)

        try:
            async with self._get_client() as s3_client:
                put_params: dict[str, Any] = {
                    "Bucket": self.bucket_name,
                    "Key": key,
                    "Body": data,
                }

                if self.tags:
                    tag_string = "&".join([f"{k}={v}" for k, v in self.tags.items()])
                    put_params["Tagging"] = tag_string

                await s3_client.put_object(**put_params)

            await logger.ainfo(f"File {file_name} saved successfully to S3: s3://{self.bucket_name}/{key}")

        except Exception as e:
            error_msg = str(e)
            error_code = None

            if hasattr(e, "response") and isinstance(e.response, dict):
                error_info = e.response.get("Error", {})
                error_code = error_info.get("Code")
                error_msg = error_info.get("Message", str(e))

            # 排障：将常见 `S3` 错误码映射为明确异常，便于调用方处理
            logger.exception(f"Error saving file {file_name} to S3 in flow {flow_id}: {error_msg}")

            if error_code == "NoSuchBucket":
                msg = f"S3 bucket '{self.bucket_name}' does not exist"
                raise FileNotFoundError(msg) from e
            if error_code == "AccessDenied":
                msg = "Access denied to S3 bucket. Please check your AWS credentials and bucket permissions"
                raise PermissionError(msg) from e
            if error_code == "InvalidAccessKeyId":
                msg = "Invalid AWS credentials. Please check your AWS access key and secret key"
                raise PermissionError(msg) from e
            msg = f"Failed to save file to S3: {error_msg}"
            raise RuntimeError(msg) from e

    async def get_file(self, flow_id: str, file_name: str) -> bytes:
        """从 `S3` 读取文件并返回字节内容。

        关键路径（三步）：
        1) 构建对象键
        2) 调用 `get_object` 读取内容
        3) 返回内容并记录日志

        异常流：对象不存在时抛 `FileNotFoundError`。
        性能瓶颈：网络下载与对象读取。
        排障入口：日志关键字 `Error retrieving file`。
        """
        key = self.build_full_path(flow_id, file_name)

        try:
            async with self._get_client() as s3_client:
                response = await s3_client.get_object(Bucket=self.bucket_name, Key=key)
                content = await response["Body"].read()

            logger.debug(f"File {file_name} retrieved successfully from S3: s3://{self.bucket_name}/{key}")
        except Exception as e:
            if hasattr(e, "response") and e.response.get("Error", {}).get("Code") == "NoSuchKey":
                await logger.awarning(f"File {file_name} not found in S3 flow {flow_id}")
                msg = f"File not found: {file_name}"
                raise FileNotFoundError(msg) from e

            logger.exception(f"Error retrieving file {file_name} from S3 in flow {flow_id}")
            raise
        else:
            return content

    async def get_file_stream(self, flow_id: str, file_name: str, chunk_size: int = 8192) -> AsyncIterator[bytes]:
        """以流式分块读取 `S3` 对象。

        关键路径（三步）：
        1) 调用 `get_object` 获取 `Body`
        2) 逐块迭代并 `yield`
        3) 关闭 `Body` 句柄

        异常流：对象不存在时抛 `FileNotFoundError`。
        性能瓶颈：网络流式读取与分块迭代。
        排障入口：日志关键字 `Error streaming file`。
        """
        key = self.build_full_path(flow_id, file_name)

        try:
            async with self._get_client() as s3_client:
                response = await s3_client.get_object(Bucket=self.bucket_name, Key=key)
                body = response["Body"]

                try:
                    async for chunk in body.iter_chunks(chunk_size):
                        yield chunk
                finally:
                    # 注意：关闭 `Body` 以释放连接与资源
                    if hasattr(body, "close"):
                        with contextlib.suppress(Exception):
                            await body.close()

        except Exception as e:
            if hasattr(e, "response") and e.response.get("Error", {}).get("Code") == "NoSuchKey":
                await logger.awarning(f"File {file_name} not found in S3 flow {flow_id}")
                msg = f"File not found: {file_name}"
                raise FileNotFoundError(msg) from e

            logger.exception(f"Error streaming file {file_name} from S3 in flow {flow_id}")
            raise

    async def list_files(self, flow_id: str) -> list[str]:
        """列出指定 `flow_id` 前缀下的文件名。

        关键路径（三步）：
        1) 计算 `prefix` 并分页遍历
        2) 去除前缀并收集文件名
        3) 返回文件名列表

        异常流：分页或请求异常时抛出原异常。
        性能瓶颈：`S3` 分页请求次数。
        排障入口：日志关键字 `Error listing files`。
        """
        if not isinstance(flow_id, str):
            flow_id = str(flow_id)

        prefix = self.build_full_path(flow_id, "")

        try:
            async with self._get_client() as s3_client:
                paginator = s3_client.get_paginator("list_objects_v2")
                files = []

                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    if "Contents" in page:
                        for obj in page["Contents"]:
                            full_key = obj["Key"]
                            file_name = full_key[len(prefix) :]
                            if file_name:
                                files.append(file_name)

        except Exception:
            logger.exception(f"Error listing files in S3 flow {flow_id}")
            raise
        else:
            return files

    async def delete_file(self, flow_id: str, file_name: str) -> None:
        """删除 `S3` 对象。

        注意：`S3` 的 `delete_object` 对不存在对象不会报错。
        """
        key = self.build_full_path(flow_id, file_name)

        try:
            async with self._get_client() as s3_client:
                await s3_client.delete_object(Bucket=self.bucket_name, Key=key)

        except Exception:
            logger.exception(f"Error deleting file {file_name} from S3 in flow {flow_id}")
            raise

    async def get_file_size(self, flow_id: str, file_name: str) -> int:
        """获取 `S3` 对象大小（字节数）。

        关键路径（三步）：
        1) 调用 `head_object` 获取元信息
        2) 读取 `ContentLength`
        3) 返回大小

        异常流：对象不存在时抛 `FileNotFoundError`。
        性能瓶颈：`head_object` 请求延迟。
        排障入口：日志关键字 `Error getting file size`。
        """
        key = self.build_full_path(flow_id, file_name)

        try:
            async with self._get_client() as s3_client:
                response = await s3_client.head_object(Bucket=self.bucket_name, Key=key)
                file_size = response["ContentLength"]

        except Exception as e:
            if hasattr(e, "response") and e.response.get("Error", {}).get("Code") in ["NoSuchKey", "404"]:
                await logger.awarning(f"File {file_name} not found in S3 flow {flow_id}")
                msg = f"File not found: {file_name}"
                raise FileNotFoundError(msg) from e

            logger.exception(f"Error getting file size for {file_name} in S3 flow {flow_id}")
            raise
        else:
            return file_size

    async def teardown(self) -> None:
        """服务销毁时的清理入口（`S3` 无额外动作）。"""
        logger.info("S3 storage service teardown complete")
