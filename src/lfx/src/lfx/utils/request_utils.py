"""模块名称：请求头工具

模块目的：统一 User-Agent 的来源与默认回退策略。
主要功能：从设置服务读取 User-Agent，失败时使用默认值。
使用场景：对外 HTTP 请求需要一致的标识。
关键组件：`get_user_agent`
设计背景：避免各处重复读取配置并处理缺省值。
注意事项：设置服务缺失或字段异常时使用默认值。
"""

from lfx.services.deps import get_settings_service

DEFAULT_USER_AGENT = "Langflow"


def get_user_agent():
    """获取 User-Agent，失败时回退默认值。"""
    try:
        settings_service = get_settings_service()
        if (
            settings_service
            and hasattr(settings_service, "settings")
            and hasattr(settings_service.settings, "user_agent")
        ):
            return settings_service.settings.user_agent
    except (AttributeError, TypeError):
        pass
    return DEFAULT_USER_AGENT
