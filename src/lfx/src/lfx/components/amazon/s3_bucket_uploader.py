"""
模块名称：S3 文件上传组件

本模块提供将文件或文本内容写入 S3 的组件封装，支持两种上传策略。主要功能包括：
- 按文本内容写入 S3（Store Data）
- 按原始文件路径上传（Store Original File）

关键组件：
- `S3BucketUploaderComponent`

设计背景：需要在 LFX 流程中将处理结果或源文件写入 S3。
使用场景：知识库构建、结果归档、文件备份。
注意事项：依赖 `boto3`，缺失将抛 `ImportError`。
"""

from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    HandleInput,
    Output,
    SecretStrInput,
    StrInput,
)


class S3BucketUploaderComponent(Component):
    """S3 文件上传组件

    契约：输入 AWS 凭证、`bucket_name`、`strategy` 与 `data_inputs`；输出写入结果标记；
    副作用：向 S3 写入对象或上传文件；失败语义：依赖缺失抛 `ImportError`，上传失败抛异常透传。
    关键路径：1) 选择上传策略 2) 遍历 `data_inputs` 3) 通过 S3 客户端写入。
    决策：提供“Store Data/Store Original File”两种策略。
    问题：既要支持文本写入，又要支持原文件直传。
    方案：用 `strategy` 分支选择处理方式。
    代价：输入数据需包含 `file_path` 与/或 `text` 字段。
    重评：当统一为单一上传方式或引入批量上传时。
    """

    display_name = "S3 Bucket Uploader"
    description = "Uploads files to S3 bucket."
    icon = "Amazon"
    name = "s3bucketuploader"

    inputs = [
        SecretStrInput(
            name="aws_access_key_id",
            display_name="AWS Access Key ID",
            required=True,
            password=True,
            info="AWS Access key ID.",
        ),
        SecretStrInput(
            name="aws_secret_access_key",
            display_name="AWS Secret Key",
            required=True,
            password=True,
            info="AWS Secret Key.",
        ),
        StrInput(
            name="bucket_name",
            display_name="Bucket Name",
            info="Enter the name of the bucket.",
            advanced=False,
        ),
        DropdownInput(
            name="strategy",
            display_name="Strategy for file upload",
            options=["Store Data", "Store Original File"],
            value="By Data",
            info=(
                "Choose the strategy to upload the file. By Data means that the source file "
                "is parsed and stored as LangFlow data. By File Name means that the source "
                "file is uploaded as is."
            ),
        ),
        HandleInput(
            name="data_inputs",
            display_name="Data Inputs",
            info="The data to split.",
            input_types=["Data"],
            is_list=True,
            required=True,
        ),
        StrInput(
            name="s3_prefix",
            display_name="S3 Prefix",
            info="Prefix for all files.",
            advanced=True,
        ),
        BoolInput(
            name="strip_path",
            display_name="Strip Path",
            info="Removes path from file path.",
            required=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Writes to AWS Bucket", name="data", method="process_files"),
    ]

    def process_files(self) -> None:
        """按策略分发上传流程

        契约：读取 `strategy` 并调用对应方法；副作用：执行上传、记录日志；
        失败语义：非法策略记录日志但不抛异常。
        关键路径：1) 构建策略映射 2) 调用对应处理函数。
        决策：非法策略仅记录日志而非中断。
        问题：组件运行时需避免因配置错误直接终止流程。
        方案：降级为日志提示。
        代价：可能掩盖配置问题。
        重评：当需要强制失败以提升可见性时。
        """
        strategy_methods = {
            "Store Data": self.process_files_by_data,
            "Store Original File": self.process_files_by_name,
        }
        strategy_methods.get(self.strategy, lambda: self.log("Invalid strategy"))()

    def process_files_by_data(self) -> None:
        """以文本内容写入 S3

        契约：从 `data_inputs` 读取 `file_path` 与 `text`，写入 `bucket_name`；
        副作用：创建对象并上传；失败语义：S3 写入失败异常透传。
        关键路径：1) 读取数据项 2) 规范化路径 3) 调用 `put_object`。
        决策：仅当 `file_path` 与 `text` 同时存在时写入。
        问题：数据项可能不完整。
        方案：缺失字段直接跳过。
        代价：不完整数据不会产生任何输出。
        重评：当需要对缺失字段进行告警或失败时。
        """
        for data_item in self.data_inputs:
            file_path = data_item.data.get("file_path")
            text_content = data_item.data.get("text")

            if file_path and text_content:
                self._s3_client().put_object(
                    Bucket=self.bucket_name, Key=self._normalize_path(file_path), Body=text_content
                )

    def process_files_by_name(self) -> None:
        """按原始文件路径上传

        契约：从 `data_inputs` 读取 `file_path` 并上传到 `bucket_name`；
        副作用：上传文件、记录日志；失败语义：上传失败异常透传。
        关键路径：1) 读取 `file_path` 2) 记录日志 3) 调用 `upload_file`。
        决策：使用本地路径作为上传源。
        问题：需要保留原始文件内容与结构。
        方案：直接上传本地文件。
        代价：要求运行环境可访问源文件路径。
        重评：当源文件不可访问或需流式上传时。
        """
        for data_item in self.data_inputs:
            file_path = data_item.data.get("file_path")
            self.log(f"Uploading file: {file_path}")
            if file_path:
                self._s3_client().upload_file(file_path, Bucket=self.bucket_name, Key=self._normalize_path(file_path))

    def _s3_client(self) -> Any:
        """创建 S3 客户端

        契约：使用组件内的 AWS 凭证创建并返回 boto3 客户端；
        副作用：加载 boto3；失败语义：缺失依赖抛 `ImportError`。
        关键路径：导入 boto3 并创建客户端。
        决策：每次调用都创建新的客户端实例。
        问题：避免跨线程或跨请求共享客户端导致的状态问题。
        方案：按需创建短生命周期客户端。
        代价：频繁调用会增加创建成本。
        重评：当需要连接复用或性能优化时。
        """
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Please install it using `uv pip install boto3`."
            raise ImportError(msg) from e

        return boto3.client(
            "s3",
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
        )

    def _normalize_path(self, file_path) -> str:
        """规范化 S3 对象 Key

        契约：输入本地 `file_path`，结合 `s3_prefix`/`strip_path` 返回对象 Key；
        副作用：无；失败语义：无（路径不存在也不会报错）。
        关键路径：1) 根据 `strip_path` 取文件名 2) 拼接 `s3_prefix`。
        决策：`strip_path=True` 时仅保留文件名。
        问题：保留本地目录结构可能导致 Key 过长或泄露路径。
        方案：可选择只保留文件名。
        代价：可能造成同名文件覆盖。
        重评：当需要保留目录层级或加入唯一后缀时。
        """
        prefix = self.s3_prefix
        strip_path = self.strip_path
        processed_path: str = file_path

        if strip_path:
            processed_path = Path(file_path).name

        if prefix:
            processed_path = str(Path(prefix) / processed_path)

        return processed_path
