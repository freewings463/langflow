"""
模块名称：settings.utils

本模块提供密钥生成、权限设置与密钥文件读写的底层工具。
主要功能包括：
- 生成 RSA 密钥对与公钥推导
- 根据操作系统设置安全文件权限
- 读写密钥文件并记录异常

关键组件：
- generate_rsa_key_pair：生成 RSA 2048 位密钥对
- derive_public_key_from_private：从私钥推导公钥
- set_secure_permissions：设置最小权限文件访问

设计背景：鉴权密钥需要落盘与复用，必须控制权限避免泄露。
注意事项：Windows 权限设置依赖 pywin32，缺失会导致异常或降级。
"""

import platform
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from lfx.log.logger import logger


class RSAKeyError(Exception):
    """RSA 密钥相关操作失败时抛出。"""


def derive_public_key_from_private(private_key_pem: str) -> str:
    """从私钥 PEM 推导公钥 PEM。

    契约：
    - 输入：`private_key_pem`（PEM 字符串）
    - 输出：公钥 PEM 字符串
    - 副作用：无
    - 失败语义：私钥解析失败时抛出 `RSAKeyError`
    """
    try:
        private_key = load_pem_private_key(private_key_pem.encode(), password=None)
        return (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )
    except Exception as e:
        msg = f"Failed to derive public key from private key: {e}"
        logger.error(msg)
        raise RSAKeyError(msg) from e


def generate_rsa_key_pair() -> tuple[str, str]:
    """生成 RSA 2048 位密钥对（用于 RS256/RS512）。

    契约：
    - 输入：无
    - 输出：`(private_key_pem, public_key_pem)`
    - 副作用：无
    - 失败语义：底层加密库异常会向上传递
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_key_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_key_pem, public_key_pem


def set_secure_permissions(file_path: Path) -> None:
    """按操作系统设置最小可写权限。

    关键路径：
    1) Linux/Darwin 直接 `chmod 0o600`
    2) Windows 设置 DACL，仅保留当前用户读写
    3) 其他系统记录错误日志

    异常流：Windows 权限设置依赖 `pywin32`，缺失会抛异常。
    """
    if platform.system() in {"Linux", "Darwin"}:  # Unix 系统
        file_path.chmod(0o600)
    elif platform.system() == "Windows":
        import win32api
        import win32con
        import win32security

        user, _, _ = win32security.LookupAccountName("", win32api.GetUserName())
        sd = win32security.GetFileSecurity(str(file_path), win32security.DACL_SECURITY_INFORMATION)
        dacl = win32security.ACL()

        # 注意：仅授予当前用户读写权限，移除其他主体的访问
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            user,
        )
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(str(file_path), win32security.DACL_SECURITY_INFORMATION, sd)
    else:
        logger.error("Unsupported OS")


def write_secret_to_file(path: Path, value: str) -> None:
    """写入密钥并尝试设置最小权限。"""
    path.write_text(value, encoding="utf-8")
    try:
        set_secure_permissions(path)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to set secure permissions on secret key")


def read_secret_from_file(path: Path) -> str:
    """读取密钥文件内容。"""
    return path.read_text(encoding="utf-8")


def write_public_key_to_file(path: Path, value: str) -> None:
    """写入公钥并设置可读权限（Unix: 0o644）。

    契约：
    - 输入：文件路径与公钥文本
    - 输出：无
    - 副作用：写入磁盘并调整权限
    - 失败语义：权限设置失败记录日志，写入本身异常会向上传递
    """
    path.write_text(value, encoding="utf-8")
    try:
        if platform.system() in {"Linux", "Darwin"}:
            path.chmod(0o644)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to set permissions on public key file")
