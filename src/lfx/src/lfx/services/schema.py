"""
模块名称：服务类型枚举

本模块定义所有服务类型枚举，用于注册与依赖注入。
主要功能包括：
- 统一服务类型标识符
- 作为服务注册与获取的键

设计背景：避免服务名称分散导致不一致。
注意事项：新增服务需在此枚举中注册。
"""

from enum import Enum


class ServiceType(str, Enum):
    """服务类型枚举。"""
    DATABASE_SERVICE = "database_service"
    STORAGE_SERVICE = "storage_service"
    SETTINGS_SERVICE = "settings_service"
    VARIABLE_SERVICE = "variable_service"
    CACHE_SERVICE = "cache_service"
    TELEMETRY_SERVICE = "telemetry_service"
    TRACING_SERVICE = "tracing_service"
    STATE_SERVICE = "state_service"
    SESSION_SERVICE = "session_service"
    CHAT_SERVICE = "chat_service"
    TASK_SERVICE = "task_service"
    STORE_SERVICE = "store_service"
    JOB_QUEUE_SERVICE = "job_queue_service"
    SHARED_COMPONENT_CACHE_SERVICE = "shared_component_cache_service"
    MCP_COMPOSER_SERVICE = "mcp_composer_service"
    TRANSACTION_SERVICE = "transaction_service"
