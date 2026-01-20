"""
模块名称：registered_email_util

本模块提供注册邮箱相关的实用函数和缓存机制，主要用于管理和检索注册邮箱地址。
主要功能包括：
- 在内存中缓存注册邮箱地址
- 提供邮箱模型的获取和设置方法
- 加载和解析邮箱注册信息

设计背景：在应用中需要有效地管理和缓存注册邮箱地址，避免重复加载
注意事项：使用单例模式的缓存机制来存储邮箱信息
"""

from lfx.log.logger import logger

from langflow.api.v2.registration import load_registration
from langflow.services.telemetry.schema import EmailPayload


class _RegisteredEmailCache:
    """注册邮箱地址的内存缓存。
    
    内部类，用于在内存中缓存注册的邮箱地址，避免重复加载和解析。
    """

    # 静态变量
    _email_model: EmailPayload | None = None

    # 静态变量
    # - True: 已通过下游源解析了注册邮箱地址（无论是否定义）
    # - False: 尚未解析注册邮箱地址
    _resolved: bool = False

    @classmethod
    def get_email_model(cls) -> EmailPayload | None:
        """从缓存中检索注册的邮箱地址。
        
        返回:
            EmailPayload | None: 缓存中的邮箱模型，如果不存在则返回None
        """
        return cls._email_model

    @classmethod
    def set_email_model(cls, value: EmailPayload | None) -> None:
        """在缓存中存储注册的邮箱地址。
        
        参数:
            value: EmailPayload对象或None
        """
        cls._email_model = value
        cls._resolved = True

    @classmethod
    def is_resolved(cls) -> bool:
        """确定是否已从下游源解析了注册邮箱地址。
        
        返回:
            bool: 如果已解析则返回True，否则返回False
        """
        return cls._resolved


def get_email_model() -> EmailPayload | None:
    """检索注册邮箱地址模型。
    
    关键路径（三步）：
    1) 检查缓存中是否已有邮箱模型
    2) 从注册源加载邮箱信息
    3) 解析并缓存邮箱模型
    
    异常流：捕获并记录加载注册信息时的异常
    性能瓶颈：注册信息的加载和解析
    排障入口：检查返回的邮箱模型是否有效
    """
    # Use cached email address from a previous invocation (if applicable)
    email = _RegisteredEmailCache.get_email_model()

    if email:
        return email

    if _RegisteredEmailCache.is_resolved():
        # No registered email address
        # OR an email address parsing error occurred
        return None

    # Retrieve registration
    try:
        registration = load_registration()
    except (OSError, AttributeError, TypeError, MemoryError) as e:
        _RegisteredEmailCache.set_email_model(None)
        logger.error(f"Failed to load email registration: {e}")
        return None

    # Parse email address from registration
    email_model = _parse_email_registration(registration)

    # Cache email address
    _RegisteredEmailCache.set_email_model(email_model)

    return email_model


def _parse_email_registration(registration) -> EmailPayload | None:
    """从注册信息中解析邮箱地址。
    
    关键路径（三步）：
    1) 验证注册信息是否定义且为字典类型
    2) 从注册信息中获取邮箱地址
    3) 创建邮箱模型
    
    异常流：未定义或无效的注册信息返回None
    性能瓶颈：无显著性能瓶颈
    排障入口：检查注册信息的格式和邮箱字段是否存在
    """
    # Verify registration is defined
    if registration is None:
        logger.debug("Email registration is not defined.")
        return None

    # Verify registration is a dict
    if not isinstance(registration, dict):
        logger.error("Email registration is not a valid dict.")
        return None

    # Retrieve email address
    email = registration.get("email")

    # Create email model
    email_model: EmailPayload | None = _create_email_model(email)

    return email_model


def _create_email_model(email) -> EmailPayload | None:
    """为注册邮箱创建模型。
    
    关键路径（三步）：
    1) 验证邮箱地址是否为有效的非空字符串
    2) 尝试创建EmailPayload实例
    3) 返回邮箱模型或None
    
    异常流：无效邮箱格式会引发ValueError并记录错误
    性能瓶颈：无显著性能瓶颈
    排障入口：检查邮箱字符串格式是否符合要求
    """
    # Verify email address is a valid non-zero length string
    if not isinstance(email, str) or (len(email) == 0):
        logger.error(f"Email is not a valid non-zero length string: {email}.")
        return None

    # Verify email address is syntactically valid
    email_model: EmailPayload | None = None

    try:
        email_model = EmailPayload(email=email)
    except ValueError as err:
        logger.error(f"Email is not a valid email address: {email}: {err}.")
        return None

    return email_model
