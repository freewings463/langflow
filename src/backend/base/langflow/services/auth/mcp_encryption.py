"""
模块名称：`MCP` 认证加密工具

本模块提供认证配置的敏感字段加解密能力，主要用于安全存储 `MCP` 凭据。
主要功能包括：
- 识别并加密 `SENSITIVE_FIELDS`。
- 解密并在失败时给出可诊断的错误提示。
- 提供加密判定工具避免重复加密。

关键组件：`encrypt_auth_settings`、`decrypt_auth_settings`、`is_encrypted`。
设计背景：`MCP` 凭据需要持久化存储且不应明文落盘。
使用场景：保存/读取认证设置时的加密与解密。
注意事项：加解密依赖 `settings_service` 中的密钥配置。
"""

from typing import Any

from cryptography.fernet import InvalidToken
from lfx.log.logger import logger

from langflow.services.auth import utils as auth_utils
from langflow.services.deps import get_settings_service

# 注意：以下字段在持久化前必须加密，避免明文存储。
SENSITIVE_FIELDS = [
    "oauth_client_secret",
    "api_key",
]


def encrypt_auth_settings(auth_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """加密认证配置中的敏感字段。

    契约：输入为配置字典或 `None`，输出为拷贝后的配置字典或 `None`。
    关键路径：逐字段尝试解密以判定加密状态，必要时写回密文。
    副作用：读取全局 `settings_service`；不会修改入参对象。
    失败语义：加密或解密失败时抛出异常并记录错误日志。
    决策：对已加密字段保持幂等
    问题：重复加密会导致不可解密
    方案：先尝试解密，失败则加密
    代价：每次写入多一次解密尝试
    重评：若可通过元数据标记加密状态
    """
    if auth_settings is None:
        return None

    settings_service = get_settings_service()
    encrypted_settings = auth_settings.copy()

    for field in SENSITIVE_FIELDS:
        if encrypted_settings.get(field):
            try:
                field_to_encrypt = encrypted_settings[field]
                # 注意：先尝试解密以判断是否已加密，避免二次加密导致不可读。
                try:
                    result = auth_utils.decrypt_api_key(field_to_encrypt, settings_service)
                    if not result:
                        msg = f"Failed to decrypt field {field}"
                        raise ValueError(msg)

                    # 注意：解密成功说明已加密，保持原值。
                    logger.debug(f"Field {field} is already encrypted")
                except (ValueError, TypeError, KeyError, InvalidToken):
                    # 注意：解密失败说明为明文，需要加密后写回。
                    encrypted_value = auth_utils.encrypt_api_key(field_to_encrypt, settings_service)
                    encrypted_settings[field] = encrypted_value
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Failed to encrypt field {field}: {e}")
                raise

    return encrypted_settings


def decrypt_auth_settings(auth_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """解密认证配置中的敏感字段。

    契约：输入为加密配置字典或 `None`，输出为解密后的字典或 `None`。
    关键路径：逐字段解密并在失败时判断是否可视为明文。
    副作用：读取全局 `settings_service`；不会修改入参对象。
    失败语义：密钥配置错误或解密失败会抛 `ValueError` 并保留上下文。
    决策：对疑似明文保持兼容
    问题：历史数据可能未加密
    方案：判断前缀特征并保留原值
    代价：误判明文可能降低安全性
    重评：当全部历史数据完成加密迁移
    """
    if auth_settings is None:
        return None

    settings_service = get_settings_service()
    decrypted_settings = auth_settings.copy()

    for field in SENSITIVE_FIELDS:
        if decrypted_settings.get(field):
            try:
                field_to_decrypt = decrypted_settings[field]

                decrypted_value = auth_utils.decrypt_api_key(field_to_decrypt, settings_service)
                if not decrypted_value:
                    msg = f"Failed to decrypt field {field}"
                    raise ValueError(msg)

                decrypted_settings[field] = decrypted_value
            except (ValueError, TypeError, KeyError, InvalidToken) as e:
                # 注意：解密失败先判断是否像加密值，避免误报明文。
                field_value = field_to_decrypt
                if isinstance(field_value, str) and field_value.startswith("gAAAAAB"):
                    # 注意：看似加密但解密失败，通常是密钥配置错误。
                    logger.error(f"Failed to decrypt encrypted field {field}: {e}")
                    msg = f"Unable to decrypt {field}. Check encryption key configuration."
                    raise ValueError(msg) from e

                # 注意：不符合加密特征则按明文兼容，保留原值。
                logger.debug(f"Field {field} appears to be plaintext, keeping original value")

    return decrypted_settings


def is_encrypted(value: str) -> bool:
    """判断字符串是否可能为已加密值。

    契约：输入为字符串，输出为布尔值；副作用：读取 `settings_service` 并尝试解密。
    关键路径：尝试解密成功即判定为已加密。
    失败语义：解密失败返回 `False`，不抛异常。
    决策：以可解密性判断是否加密
    问题：无法可靠区分密文与随机字符串
    方案：尝试解密成功即视为加密
    代价：需要一次解密尝试
    重评：若引入显式加密标记或元数据
    """
    if not value:
        return False

    settings_service = get_settings_service()
    try:
        # 注意：解密成功即认为已加密。
        auth_utils.decrypt_api_key(value, settings_service)
    except (ValueError, TypeError, KeyError, InvalidToken):
        # 注意：解密失败视为未加密。
        return False
    else:
        return True
