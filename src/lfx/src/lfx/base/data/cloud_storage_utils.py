"""
模块名称：cloud_storage_utils

本模块提供云存储（AWS S3、Google Drive）通用工具，供读写组件共享。
主要功能包括：
- 功能1：校验 AWS S3 凭据完整性并创建客户端。
- 功能2：解析 Google 服务账号 JSON 并构建 Drive API 服务。

使用场景：需要在组件内按需接入 S3 或 Google Drive 时。
关键组件：
- 函数 `validate_aws_credentials`
- 函数 `create_s3_client`
- 函数 `parse_google_service_account_key`
- 函数 `create_google_drive_service`

设计背景：读写组件共享认证与客户端构建逻辑，避免重复与不一致。
注意事项：依赖为可选安装，缺失时会抛 `ImportError`。
"""

from __future__ import annotations

import json
from typing import Any


def validate_aws_credentials(component: Any) -> None:
    """校验 AWS S3 凭据是否完整。

    契约：读取 `component` 上的 `aws_access_key_id`/`aws_secret_access_key`/`bucket_name`。
    关键路径：依次检查必填字段并在首个缺失处抛错。
    异常流：任一字段缺失时抛 `ValueError`。
    决策：
    问题：凭据缺失会在后续 API 调用处报错且难定位。
    方案：在客户端创建前做显式校验。
    代价：增加一次运行期校验。
    重评：当统一凭据管理层提供结构化校验时。
    """
    if not getattr(component, "aws_access_key_id", None):
        msg = "AWS Access Key ID is required for S3 storage"
        raise ValueError(msg)
    if not getattr(component, "aws_secret_access_key", None):
        msg = "AWS Secret Key is required for S3 storage"
        raise ValueError(msg)
    if not getattr(component, "bucket_name", None):
        msg = "S3 Bucket Name is required for S3 storage"
        raise ValueError(msg)


def create_s3_client(component: Any):
    """创建并返回 boto3 S3 客户端。

    契约：依赖 `boto3`；使用组件上的凭据与可选 `aws_region`。
    关键路径：延迟导入 `boto3` -> 组装配置 -> 创建客户端。
    副作用：导入 `boto3` 并创建网络客户端对象。
    异常流：未安装 `boto3` 时抛 `ImportError`。
    决策：
    问题：S3 依赖为可选包，不应强制引入。
    方案：在函数内延迟导入 `boto3`。
    代价：首次调用时可能有导入开销。
    重评：当依赖改为强制安装时。
    """
    try:
        import boto3
    except ImportError as e:
        msg = "boto3 is not installed. Please install it using `uv pip install boto3`."
        raise ImportError(msg) from e

    client_config = {
        "aws_access_key_id": component.aws_access_key_id,
        "aws_secret_access_key": component.aws_secret_access_key,
    }

    if hasattr(component, "aws_region") and component.aws_region:
        client_config["region_name"] = component.aws_region

    return boto3.client("s3", **client_config)


def parse_google_service_account_key(service_account_key: str) -> dict:
    """解析 Google 服务账号 JSON，包含多种容错策略。

    契约：输入为 JSON 字符串；输出字典；失败时抛 `ValueError`。
    关键路径：
    1) 原样解析（允许控制字符）；
    2) 去除首尾空白重试；
    3) 双重 JSON 解码；
    4) 修复 `private_key` 换行后再解析。
    异常流：全部策略失败时返回包含详细原因的 `ValueError`。
    决策：
    问题：用户粘贴的服务账号 JSON 常包含格式问题。
    方案：多策略解析并记录失败原因。
    代价：解析逻辑更复杂，错误信息更长。
    重评：当仅允许文件上传而非粘贴时。
    """
    credentials_dict = None
    parse_errors = []

    # 实现：策略 1 - 原样解析，`strict=False` 允许控制字符。
    try:
        credentials_dict = json.loads(service_account_key, strict=False)
    except json.JSONDecodeError as e:
        parse_errors.append(f"Standard parse: {e!s}")

    # 实现：策略 2 - 去除首尾空白后解析。
    if credentials_dict is None:
        try:
            cleaned_key = service_account_key.strip()
            credentials_dict = json.loads(cleaned_key, strict=False)
        except json.JSONDecodeError as e:
            parse_errors.append(f"Stripped parse: {e!s}")

    # 实现：策略 3 - 处理双重编码的 JSON 字符串。
    if credentials_dict is None:
        try:
            decoded_once = json.loads(service_account_key, strict=False)
            credentials_dict = json.loads(decoded_once, strict=False) if isinstance(decoded_once, str) else decoded_once
        except json.JSONDecodeError as e:
            parse_errors.append(f"Double-encoded parse: {e!s}")

    # 实现：策略 4 - 修复 `private_key` 中被转义的换行。
    if credentials_dict is None:
        try:
            # 注意：将字面量 `\\n` 替换为真实换行，适配粘贴场景。
            fixed_key = service_account_key.replace("\\n", "\n")
            credentials_dict = json.loads(fixed_key, strict=False)
        except json.JSONDecodeError as e:
            parse_errors.append(f"Newline-fixed parse: {e!s}")

    if credentials_dict is None:
        error_details = "; ".join(parse_errors)
        msg = (
            f"Unable to parse service account key JSON. Tried multiple strategies: {error_details}. "
            "Please ensure you've copied the entire JSON content from your service account key file. "
            "The JSON should start with '{' and contain fields like 'type', 'project_id', 'private_key', etc."
        )
        raise ValueError(msg)

    return credentials_dict


def create_google_drive_service(service_account_key: str, scopes: list[str], *, return_credentials: bool = False):
    """创建并返回 Google Drive API 服务对象。

    契约：`service_account_key` 为 JSON 字符串；`scopes` 为权限列表；可选返回 `(service, credentials)`。
    关键路径：解析 JSON -> 构建凭据 -> `build` API 服务。
    副作用：导入 Google 客户端库并创建 API 连接对象。
    异常流：缺少依赖抛 `ImportError`；解析失败抛 `ValueError`。
    决策：
    问题：服务账号 JSON 质量不可控且依赖为可选安装。
    方案：先解析再创建凭据，导入失败时提供明确错误。
    代价：首次调用需加载依赖，耗时略增。
    重评：当改用统一凭据服务或默认 ADC 时。
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:
        msg = "Google API client libraries are not installed. Please install them."
        raise ImportError(msg) from e

    credentials_dict = parse_google_service_account_key(service_account_key)

    credentials = service_account.Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    service = build("drive", "v3", credentials=credentials)

    if return_credentials:
        return service, credentials
    return service
