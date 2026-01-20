"""
模块名称：授权配置更新助手

本模块封装项目授权配置的加解密与前端掩码字段处理逻辑，并输出 MCP Composer 的启停指令。
主要功能：
- 统一 `dict`/Pydantic 模型的授权配置输入
- 保留前端掩码字段对应的旧密钥
- 加密写回并计算 Composer 启停标志
设计背景：避免在 API 层重复处理敏感字段与 Composer 兼容逻辑。
注意事项：加解密失败会抛异常，调用方需回滚并提示重试。
"""

from typing import Any

from pydantic import SecretStr

from langflow.services.auth.mcp_encryption import decrypt_auth_settings, encrypt_auth_settings
from langflow.services.database.models.folder.model import Folder


def handle_auth_settings_update(
    existing_project: Folder,
    new_auth_settings: dict | Any | None,
) -> dict[str, bool]:
    """更新授权配置并计算 MCP Composer 的启停指令。

    契约：
    - 输入：`existing_project`（原地修改）、`new_auth_settings`
    - 输出：`dict[str, bool]`（含 `should_start_composer`/`should_stop_composer`/`should_handle_composer`）
    - 副作用：写回 `existing_project.auth_settings`
    - 失败语义：加解密失败抛异常；调用方应回滚事务并提示重试

    关键路径（三步）：
    1) 读取当前 `auth_type` 并按需解密旧配置
    2) 归一化新配置并还原 `SecretStr`
    3) 处理掩码字段、加密写回并计算启停标志

    排障入口：关注 `decrypt_auth_settings`/`encrypt_auth_settings` 的异常日志。
    """
    # 注意：先读取旧的 `auth_type`，用于后续启停判断与掩码字段回填。
    current_auth_type = None
    decrypted_current = None
    if existing_project.auth_settings:
        current_auth_type = existing_project.auth_settings.get("auth_type")
        # 安全：仅在需要保留敏感字段时解密，避免无谓暴露明文。
        if current_auth_type in ["oauth", "apikey"]:
            decrypted_current = decrypt_auth_settings(existing_project.auth_settings)

    if new_auth_settings is None:
        # 实现：显式清空授权配置。
        existing_project.auth_settings = None
        # 注意：从 OAuth 退出时需要停止 Composer。
        return {"should_start_composer": False, "should_stop_composer": current_auth_type == "oauth"}

    # 实现：统一处理 `dict` 与 Pydantic 模型输入。
    if isinstance(new_auth_settings, dict):
        auth_dict = new_auth_settings.copy()
    else:
        # 注意：`python` 模式可拿到未掩码的原始值。
        auth_dict = new_auth_settings.model_dump(mode="python", exclude_none=True)

        # 安全：恢复 `SecretStr` 字段真实值以便加密持久化。
        secret_fields = ["api_key", "oauth_client_secret"]
        for field in secret_fields:
            field_val = getattr(new_auth_settings, field, None)
            if isinstance(field_val, SecretStr):
                auth_dict[field] = field_val.get_secret_value()

    new_auth_type = auth_dict.get("auth_type")

    # 注意：前端可能回传 `*******`，需保留旧密钥而非覆盖。
    if decrypted_current:
        secret_fields = ["oauth_client_secret", "api_key"]
        for field in secret_fields:
            if field in auth_dict and auth_dict[field] == "*******" and field in decrypted_current:
                auth_dict[field] = decrypted_current[field]

    # 安全：仅写入加密后的授权配置。
    existing_project.auth_settings = encrypt_auth_settings(auth_dict)

    # 实现：计算 MCP Composer 启停与处理标志。
    should_start_composer = new_auth_type == "oauth"
    should_stop_composer = current_auth_type == "oauth" and new_auth_type != "oauth"
    should_handle_composer = current_auth_type == "oauth" or new_auth_type == "oauth"

    return {
        "should_start_composer": should_start_composer,
        "should_stop_composer": should_stop_composer,
        "should_handle_composer": should_handle_composer,
    }
