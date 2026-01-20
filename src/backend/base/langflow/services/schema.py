"""
模块名称：服务类型枚举定义

本模块定义了可以注册到服务管理器的不同服务类型的枚举。
主要功能包括：
- 定义所有可用的服务类型枚举值
- 为服务注册和查找提供统一标识

关键组件：
- `ServiceType`：服务类型枚举

设计背景：提供统一的服务类型标识，便于服务注册和查找。
注意事项：每个服务类型应该是唯一的，添加新服务时需要在此添加对应类型。
"""

from enum import Enum


class ServiceType(str, Enum):
    """服务管理器中可注册的不同服务类型的枚举。

    契约：提供服务类型的统一标识。
    副作用：无。
    失败语义：无。
    
    决策：使用字符串枚举
    问题：需要统一标识服务类型
    方案：使用 str 和 Enum 的组合
    代价：类型安全略有降低
    重评：如果需要更强的类型安全则考虑其他方案
    """
    AUTH_SERVICE = "auth_service"
    CACHE_SERVICE = "cache_service"
    SHARED_COMPONENT_CACHE_SERVICE = "shared_component_cache_service"
    SETTINGS_SERVICE = "settings_service"
    DATABASE_SERVICE = "database_service"
    CHAT_SERVICE = "chat_service"
    SESSION_SERVICE = "session_service"
    TASK_SERVICE = "task_service"
    STORE_SERVICE = "store_service"
    VARIABLE_SERVICE = "variable_service"
    STORAGE_SERVICE = "storage_service"
    STATE_SERVICE = "state_service"
    TRACING_SERVICE = "tracing_service"
    TELEMETRY_SERVICE = "telemetry_service"
    JOB_QUEUE_SERVICE = "job_queue_service"
    MCP_COMPOSER_SERVICE = "mcp_composer_service"