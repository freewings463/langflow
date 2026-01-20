"""
模块名称：settings.auth

本模块定义鉴权相关设置与密钥管理逻辑，负责 JWT 算法选择与密钥持久化。
主要功能包括：
- JWT 算法枚举与对称/非对称判断
- 鉴权设置模型与默认值
- SECRET_KEY 与 RSA 密钥的生成、加载与落盘

关键组件：
- JWTAlgorithm：JWT 签名算法枚举
- AuthSettings：鉴权设置模型
- get_secret_key/setup_rsa_keys：密钥解析与持久化流程

设计背景：鉴权配置需要与部署环境解耦，并对密钥生命周期提供统一入口。
注意事项：AUTO_LOGIN 仅适用于开发环境，生产环境必须关闭并显式配置密钥。
"""

import secrets
from enum import Enum
from pathlib import Path
from typing import Literal

from passlib.context import CryptContext
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lfx.log.logger import logger
from lfx.services.settings.constants import DEFAULT_SUPERUSER, DEFAULT_SUPERUSER_PASSWORD
from lfx.services.settings.utils import (
    derive_public_key_from_private,
    generate_rsa_key_pair,
    read_secret_from_file,
    write_public_key_to_file,
    write_secret_to_file,
)


class JWTAlgorithm(str, Enum):
    """JWT 签名算法枚举。"""

    HS256 = "HS256"
    RS256 = "RS256"
    RS512 = "RS512"

    def is_asymmetric(self) -> bool:
        """判断当前算法是否为非对称加密。"""
        return self in (JWTAlgorithm.RS256, JWTAlgorithm.RS512)


class AuthSettings(BaseSettings):
    """鉴权设置模型。

    契约：
    - 输入：环境变量与显式构造参数
    - 输出：可用于鉴权组件的配置对象
    - 副作用：可能读写密钥文件（`CONFIG_DIR` 下）
    - 失败语义：密钥解析失败会抛出异常并中断启动
    """

    # 登录相关设置
    CONFIG_DIR: str
    SECRET_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Secret key for JWT (used with HS256). If not provided, a random one will be generated.",
        frozen=False,
    )
    PRIVATE_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="RSA private key for JWT signing (RS256/RS512). Auto-generated if not provided.",
        frozen=False,
    )
    PUBLIC_KEY: str = Field(
        default="",
        description="RSA public key for JWT verification (RS256/RS512). Derived from private key if not provided.",
    )
    ALGORITHM: JWTAlgorithm = Field(
        default=JWTAlgorithm.HS256,
        description="JWT signing algorithm. Use RS256 or RS512 for asymmetric signing (recommended for production).",
    )
    ACCESS_TOKEN_EXPIRE_SECONDS: int = 60 * 60  # 1 小时
    REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 7  # 7 天

    # 用于 /process 端点的 API Key
    API_KEY_ALGORITHM: str = "HS256"
    API_V1_STR: str = "/api/v1"

    # API Key 来源配置
    API_KEY_SOURCE: Literal["db", "env"] = Field(
        default="db",
        description=(
            "Source for API key validation. "
            "'db' validates against database-stored API keys (default behavior). "
            "'env' validates against the LANGFLOW_API_KEY environment variable."
        ),
    )

    AUTO_LOGIN: bool = Field(
        default=True,  # TODO：v2.0 将默认改为 False
        description=(
            "Enable automatic login with default credentials. "
            "SECURITY WARNING: This bypasses authentication and should only be used in development environments. "
            "Set to False in production. This will default to False in v2.0."
        ),
    )
    """是否启用默认超级用户自动登录（仅建议开发环境）。"""
    skip_auth_auto_login: bool = False
    """若为 True，AUTO_LOGIN 启用时将跳过鉴权（v2.0 计划移除）。"""

    WEBHOOK_AUTH_ENABLE: bool = False
    """是否要求 webhook 端点使用 API key 鉴权。"""

    ENABLE_SUPERUSER_CLI: bool = Field(
        default=True,
        description="Allow creation of superusers via CLI. Set to False in production for security.",
    )
    """是否允许通过 `langflow superuser` CLI 创建超级用户。"""

    NEW_USER_IS_ACTIVE: bool = False
    SUPERUSER: str = DEFAULT_SUPERUSER
    # 注意：使用 SecretStr 存储，避免日志/调试时明文泄露
    SUPERUSER_PASSWORD: SecretStr = Field(default=DEFAULT_SUPERUSER_PASSWORD)

    REFRESH_SAME_SITE: Literal["lax", "strict", "none"] = "none"
    """刷新令牌 Cookie 的 SameSite 属性。"""
    REFRESH_SECURE: bool = True
    """刷新令牌 Cookie 的 Secure 属性。"""
    REFRESH_HTTPONLY: bool = True
    """刷新令牌 Cookie 的 HttpOnly 属性。"""
    ACCESS_SAME_SITE: Literal["lax", "strict", "none"] = "lax"
    """访问令牌 Cookie 的 SameSite 属性。"""
    ACCESS_SECURE: bool = False
    """访问令牌 Cookie 的 Secure 属性。"""
    ACCESS_HTTPONLY: bool = False
    """访问令牌 Cookie 的 HttpOnly 属性。"""

    COOKIE_DOMAIN: str | None = None
    """Cookie 的 domain 属性；为 None 时不设置。"""

    pwd_context: CryptContext = CryptContext(schemes=["bcrypt"], deprecated="auto")

    model_config = SettingsConfigDict(validate_assignment=True, extra="ignore", env_prefix="LANGFLOW_")

    def reset_credentials(self) -> None:
        """清理内存中的默认密码（保留用户名）。"""
        self.SUPERUSER_PASSWORD = SecretStr("")

    @field_validator("SUPERUSER", "SUPERUSER_PASSWORD", mode="before")
    @classmethod
    def validate_superuser(cls, value, info):
        """在 AUTO_LOGIN 启用时强制回退到默认凭据。"""
        if info.data.get("AUTO_LOGIN"):
            logger.debug("Auto login is enabled, forcing superuser to use default values")
            if info.field_name == "SUPERUSER":
                if value != DEFAULT_SUPERUSER:
                    logger.debug("Resetting superuser to default value")
                return DEFAULT_SUPERUSER
            if info.field_name == "SUPERUSER_PASSWORD":
                if value != DEFAULT_SUPERUSER_PASSWORD.get_secret_value():
                    logger.debug("Resetting superuser password to default value")
                return DEFAULT_SUPERUSER_PASSWORD

        return value

    @field_validator("SECRET_KEY", mode="before")
    @classmethod
    def get_secret_key(cls, value, info):
        """获取或生成对称密钥并按需落盘。

        关键路径：
        1) 若传入值则直接落盘
        2) 否则尝试从 `CONFIG_DIR/secret_key` 读取
        3) 仍为空则生成随机密钥并保存

        异常流：写文件失败会抛出异常并中断启动。
        排障入口：日志关键字 `secret key`。
        """
        config_dir = info.data.get("CONFIG_DIR")

        if not config_dir:
            logger.debug("No CONFIG_DIR provided, not saving secret key")
            return value or secrets.token_urlsafe(32)

        secret_key_path = Path(config_dir) / "secret_key"

        if value:
            logger.debug("Secret key provided")
            secret_value = value.get_secret_value() if isinstance(value, SecretStr) else value
            write_secret_to_file(secret_key_path, secret_value)
        elif secret_key_path.exists():
            value = read_secret_from_file(secret_key_path)
            logger.debug("Loaded secret key")
            if not value:
                value = secrets.token_urlsafe(32)
                write_secret_to_file(secret_key_path, value)
                logger.debug("Saved secret key")
        else:
            value = secrets.token_urlsafe(32)
            write_secret_to_file(secret_key_path, value)
            logger.debug("Saved secret key")

        return value if isinstance(value, SecretStr) else SecretStr(value).get_secret_value()

    @model_validator(mode="after")
    def setup_rsa_keys(self):
        """在使用 RS256/RS512 时生成或加载 RSA 密钥。

        关键路径：
        1) 无 `CONFIG_DIR` 时仅在内存生成/推导
        2) 有私钥则写入并推导公钥
        3) 无私钥则从磁盘加载或生成新密钥对

        异常流：私钥解析失败会抛出 `RSAKeyError`。
        排障入口：日志关键字 `RSA key`/`private_key`。
        """
        if not self.ALGORITHM.is_asymmetric():
            return self

        config_dir = self.CONFIG_DIR
        private_key_value = self.PRIVATE_KEY.get_secret_value() if self.PRIVATE_KEY else ""

        if not config_dir:
            # 注意：无配置目录时仅在内存生成，进程重启会失效
            if not private_key_value:
                logger.debug("No CONFIG_DIR provided, generating RSA keys in memory")
                private_key_pem, public_key_pem = generate_rsa_key_pair()
                object.__setattr__(self, "PRIVATE_KEY", SecretStr(private_key_pem))
                object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
            elif not self.PUBLIC_KEY:
                # 注意：未提供公钥时从私钥推导
                public_key_pem = derive_public_key_from_private(private_key_value)
                object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
            return self

        private_key_path = Path(config_dir) / "private_key.pem"
        public_key_path = Path(config_dir) / "public_key.pem"

        if private_key_value:
            # 注意：私钥来自环境变量，需落盘以便后续复用
            logger.debug("RSA private key provided")
            write_secret_to_file(private_key_path, private_key_value)

            if not self.PUBLIC_KEY:
                public_key_pem = derive_public_key_from_private(private_key_value)
                object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
                write_public_key_to_file(public_key_path, public_key_pem)
        # 注意：未提供私钥则从文件加载，若不存在则生成
        elif private_key_path.exists():
            logger.debug("Loading RSA keys from files")
            private_key_pem = read_secret_from_file(private_key_path)
            object.__setattr__(self, "PRIVATE_KEY", SecretStr(private_key_pem))

            if public_key_path.exists():
                public_key_pem = public_key_path.read_text(encoding="utf-8")
                object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
            else:
                # 注意：缺失公钥文件时自动推导并补写
                public_key_pem = derive_public_key_from_private(private_key_pem)
                object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
                write_public_key_to_file(public_key_path, public_key_pem)
        else:
            # 注意：首次启动且无密钥文件时生成新密钥对
            logger.debug("Generating new RSA key pair")
            private_key_pem, public_key_pem = generate_rsa_key_pair()
            write_secret_to_file(private_key_path, private_key_pem)
            write_public_key_to_file(public_key_path, public_key_pem)
            object.__setattr__(self, "PRIVATE_KEY", SecretStr(private_key_pem))
            object.__setattr__(self, "PUBLIC_KEY", public_key_pem)
            logger.debug("RSA key pair generated and saved")

        return self
