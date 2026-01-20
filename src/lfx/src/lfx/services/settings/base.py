"""
模块名称：settings.base

本模块定义 Langflow 的核心运行配置与加载流程，集中处理环境变量、默认值与配置文件。
主要功能包括：
- Settings：统一的运行时配置模型
- CustomSource：环境变量解析扩展（支持列表与复杂值）
- YAML 配置的读写与合并

关键组件：
- Settings：核心设置模型与校验器
- load_settings_from_yaml/save_settings_to_yaml：配置持久化入口

设计背景：配置来源多且变动频繁，需要在单点统一解析并兼顾迁移兼容。
注意事项：部分校验器会执行文件读写与数据库迁移路径推断，启动阶段需关注日志。
"""

import asyncio
import contextlib
import json
import os
from pathlib import Path
from shutil import copy2
from typing import Any, Literal

import orjson
import yaml
from aiofile import async_open
from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource, SettingsConfigDict
from typing_extensions import override

from lfx.constants import BASE_COMPONENTS_PATH
from lfx.log.logger import logger
from lfx.serialization.constants import MAX_ITEMS_LENGTH, MAX_TEXT_LENGTH
from lfx.services.settings.constants import AGENTIC_VARIABLES, VARIABLES_TO_GET_FROM_ENVIRONMENT
from lfx.utils.util_strings import is_valid_database_url


def is_list_of_any(field: FieldInfo) -> bool:
    """判断字段类型是否为列表或可选列表。

    契约：
    - 输入：Pydantic `FieldInfo`
    - 输出：是否为 `list[...]` 或 `Optional[list[...]]`
    - 副作用：无
    - 失败语义：异常类型解析失败时返回 False
    """
    if field.annotation is None:
        return False
    try:
        union_args = field.annotation.__args__ if hasattr(field.annotation, "__args__") else []

        return field.annotation.__origin__ is list or any(
            arg.__origin__ is list for arg in union_args if hasattr(arg, "__origin__")
        )
    except AttributeError:
        return False


class CustomSource(EnvSettingsSource):
    """环境变量解析扩展。

    契约：
    - 输入：环境变量字符串
    - 输出：按字段类型解析后的值
    - 副作用：无
    - 失败语义：解析失败时回退到父类实现
    """

    @override
    def prepare_field_value(self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool) -> Any:  # type: ignore[misc]
        # 注意：允许逗号分隔的列表形式，降低配置门槛
        if is_list_of_any(field):
            if isinstance(value, str):
                value = value.split(",")
            if isinstance(value, list):
                return value

        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    """Langflow 运行配置集合。

    契约：
    - 输入：环境变量、配置文件、构造参数
    - 输出：可读写的配置对象
    - 副作用：部分校验器会读写磁盘或设置进程环境变量
    - 失败语义：无效配置会抛出 ValueError/KeyError
    """

    # 定义默认 LANGFLOW_DIR
    config_dir: str | None = None
    # 决策：数据库默认落盘位置随配置项切换
    save_db_in_config_dir: bool = False
    """Langflow 数据库是否存放于 `LANGFLOW_CONFIG_DIR` 而非包目录。"""

    knowledge_bases_dir: str | None = "~/.langflow/knowledge_bases"
    """知识库文件存放目录。"""

    dev: bool = False
    """是否以开发模式运行。"""
    database_url: str | None = None
    """Langflow 数据库 URL。为空时默认使用 SQLite。

    注意：会自动将 `sqlite` 与 `postgresql` 转换为异步驱动
    `sqlite+aiosqlite` 与 `postgresql+psycopg`。
    """
    database_connection_retry: bool = False
    """数据库连接失败时是否自动重试。"""
    pool_size: int = 20
    """连接池常驻连接数；高并发场景需按预期并发调大。"""
    max_overflow: int = 30
    """超出连接池的额外连接上限，建议约为 pool_size 的 2 倍。"""
    db_connect_timeout: int = 30
    """获取数据库连接或锁的超时时间（秒）。"""
    migration_lock_namespace: str | None = None
    """迁移期间 PostgreSQL advisory lock 的命名空间。

    为空时使用数据库 URL 哈希；适用于多实例共享数据库的迁移锁协调。
    """

    mcp_server_timeout: int = 20
    """MCP 服务连接/锁等待超时（秒）。"""

    # ---------------------------------------------------------------------
    # MCP 会话管理调优
    # ---------------------------------------------------------------------
    mcp_max_sessions_per_server: int = 10
    """每个 MCP 服务器保留的最大会话数（按 command/url 维度）。"""

    mcp_session_idle_timeout: int = 400  # 秒
    """MCP 会话空闲超时（秒），超时后后台回收。"""

    mcp_session_cleanup_interval: int = 120  # 秒
    """后台清理任务的唤醒频率（秒）。"""

    # SQLite 配置
    sqlite_pragmas: dict | None = {"synchronous": "NORMAL", "journal_mode": "WAL", "busy_timeout": 30000}
    """SQLite 连接时使用的 pragma 配置。"""

    db_driver_connection_settings: dict | None = None
    """数据库驱动连接设置。"""

    db_connection_settings: dict | None = {
        "pool_size": 20,  # 与上方 pool_size 保持一致
        "max_overflow": 30,  # 与上方 max_overflow 保持一致
        "pool_timeout": 30,  # 等待连接池可用连接的秒数
        "pool_pre_ping": True,  # 使用前校验连接可用性
        "pool_recycle": 1800,  # 30 分钟回收连接避免超时
        "echo": False,  # 仅调试时开启 SQL 日志
    }
    """高负载优化的数据库连接配置。

    注意：以下配置对 PostgreSQL 更有效；SQLite 场景建议降低 pool_size/max_overflow，
    因其写入并发能力有限（即便开启 WAL）。
    """

    use_noop_database: bool = False
    """是否使用空操作数据库会话（禁用所有 DB 操作）。"""

    # 缓存配置
    cache_type: Literal["async", "redis", "memory", "disk"] = "async"
    """缓存类型：`async`/`redis`/`memory`/`disk`。"""
    cache_expire: int = 3600
    """缓存过期时间（秒）。"""
    variable_store: str = "db"
    """变量存储后端，可选 `db` 或 `kubernetes`。"""

    prometheus_enabled: bool = False
    """是否暴露 Prometheus 指标。"""
    prometheus_port: int = 9090
    """Prometheus 指标端口，默认 9090。"""

    disable_track_apikey_usage: bool = False
    remove_api_keys: bool = False
    components_path: list[str] = []
    components_index_path: str | None = None
    """组件索引 JSON 文件路径或 URL。

    为空时使用内置索引 `lfx/_assets/component_index.json`。
    """
    langchain_cache: str = "InMemoryCache"
    load_flows_path: str | None = None
    bundle_urls: list[str] = []

    # Redis 配置
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_url: str | None = None
    redis_cache_expire: int = 3600

    # Sentry 配置
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float | None = 1.0
    sentry_profiles_sample_rate: float | None = 1.0

    store: bool | None = True
    store_url: str | None = "https://api.langflow.store"
    download_webhook_url: str | None = "https://api.langflow.store/flows/trigger/ec611a61-8460-4438-b187-a4f65e5559d4"
    like_webhook_url: str | None = "https://api.langflow.store/flows/trigger/64275852-ec00-45c1-984e-3bff814732da"

    storage_type: str = "local"
    """文件存储类型，支持 `local` 与 `s3`。"""
    object_storage_bucket_name: str | None = "langflow-bucket"
    """对象存储桶名称。"""
    object_storage_prefix: str | None = "files"
    """对象存储前缀。"""
    object_storage_tags: dict[str, str] | None = None
    """对象存储标签。"""

    celery_enabled: bool = False

    fallback_to_env_var: bool = True
    """UI 全局变量读取失败时是否回退同名环境变量。"""

    store_environment_variables: bool = True
    """是否将环境变量写入全局变量表。"""
    variables_to_get_from_environment: list[str] = VARIABLES_TO_GET_FROM_ENVIRONMENT
    """需要采集并存入数据库的环境变量白名单。"""
    worker_timeout: int = 300
    """API 调用超时（秒）。"""
    frontend_timeout: int = 0
    """前端 API 调用超时（秒）。"""
    user_agent: str = "langflow"
    """API 调用的 User-Agent。"""
    backend_only: bool = False
    """是否仅启动后端（不提供前端）。"""

    # CORS 设置
    cors_origins: list[str] | str = "*"
    """CORS 允许的来源列表或 `*`。生产环境建议显式配置。"""
    cors_allow_credentials: bool = True
    """CORS 是否允许凭据；使用 `*` 时默认仍为 True 以兼容历史行为。"""
    cors_allow_methods: list[str] | str = "*"
    """CORS 允许的 HTTP 方法。"""
    cors_allow_headers: list[str] | str = "*"
    """CORS 允许的请求头。"""

    # 遥测
    do_not_track: bool = False
    """是否禁用遥测上报。"""
    telemetry_base_url: str = "https://langflow.gateway.scarf.sh"
    transactions_storage_enabled: bool = True
    """是否记录 flow 之间的事务。"""
    vertex_builds_storage_enabled: bool = True
    """是否记录每个 vertex 的构建产物（UI 展示）。"""

    # 运行配置
    host: str = "localhost"
    """运行主机名。"""
    port: int = 7860
    """运行端口。"""
    runtime_port: int | None = Field(default=None, exclude=True)
    """临时端口：用于冲突检测后的实际端口（系统管理，后续版本移除）。"""
    workers: int = 1
    """运行 worker 数。"""
    log_level: str = "critical"
    """日志级别。"""
    log_file: str | None = "logs/langflow.log"
    """Langflow 日志文件路径。"""
    alembic_log_file: str = "alembic/alembic.log"
    """Alembic 日志文件路径。"""
    alembic_log_to_stdout: bool = False
    """是否将 Alembic 日志输出到 stdout。"""
    frontend_path: str | None = None
    """前端构建产物路径（仅开发用）。"""
    open_browser: bool = False
    """启动时是否自动打开浏览器。"""
    auto_saving: bool = True
    """是否自动保存流程。"""
    auto_saving_interval: int = 1000
    """自动保存间隔（毫秒）。"""
    health_check_max_retries: int = 5
    """健康检查最大重试次数。"""
    max_file_size_upload: int = 1024
    """上传文件大小上限（MB）。"""
    deactivate_tracing: bool = False
    """是否关闭追踪。"""
    max_transactions_to_keep: int = 3000
    """数据库中保留的最大事务数。"""
    max_vertex_builds_to_keep: int = 3000
    """数据库中保留的最大 vertex 构建数。"""
    max_vertex_builds_per_vertex: int = 2
    """每个 vertex 保留的最大构建数（超出将删除旧记录）。"""
    webhook_polling_interval: int = 5000
    """Webhook 轮询间隔（毫秒）。"""
    fs_flows_polling_interval: int = 10000
    """从文件系统同步流程的轮询间隔（毫秒）。"""
    ssl_cert_file: str | None = None
    """SSL 证书文件路径。"""
    ssl_key_file: str | None = None
    """SSL 私钥文件路径。"""
    max_text_length: int = MAX_TEXT_LENGTH
    """UI 展示的最大文本长度，超出将被截断（不影响组件间数据）。"""
    max_items_length: int = MAX_ITEMS_LENGTH
    """UI 展示的最大列表长度，超出将被截断（不影响组件间数据）。"""

    # MCP 服务
    mcp_server_enabled: bool = True
    """是否启用 MCP 服务。"""
    mcp_server_enable_progress_notifications: bool = False
    """是否启用 MCP 进度通知。"""

    # 创建项目时自动加入 MCP 服务配置
    add_projects_to_mcp_servers: bool = True
    """新建项目是否自动加入用户 MCP 服务配置。"""
    # MCP Composer
    mcp_composer_enabled: bool = True
    """是否启动 MCP Composer 服务。"""
    mcp_composer_version: str = "==0.1.0.8.10"
    """mcp-composer 版本约束（PEP 440）。"""

    # Agentic 体验
    agentic_experience: bool = False
    """是否启用 Agentic 体验（包含工具、模板搜索与图可视化）。"""

    # 开发者 API
    developer_api_enabled: bool = False
    """是否启用开发者 API（调试/自省）。"""

    # 公开流程设置
    public_flow_cleanup_interval: int = Field(default=3600, gt=600)
    """公开临时流程清理间隔（秒），默认 3600，最小 600。"""
    public_flow_expiration: int = Field(default=86400, gt=600)
    """公开临时流程过期时间（秒），默认 86400，最小 600。"""
    event_delivery: Literal["polling", "streaming", "direct"] = "streaming"
    """构建事件投递方式：`polling`/`streaming`/`direct`。"""
    lazy_load_components: bool = False
    """是否延迟加载组件（启动更快，但首次使用会有延迟）。"""

    # Starter 项目
    create_starter_projects: bool = True
    """是否创建 starter 项目（不检查数据库中是否已存在）。"""
    update_starter_projects: bool = True
    """是否更新 starter 项目。"""

    # SSRF 防护
    ssrf_protection_enabled: bool = False
    """是否启用 SSRF 防护（阻止私网/元数据地址）。

    注意：默认关闭以兼容历史行为；关闭时 `ssrf_allowed_hosts` 不生效。
    """
    ssrf_allowed_hosts: list[str] = []
    """SSRF 允许列表（host/IP/CIDR），仅在防护启用时生效。"""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def validate_cors_origins(cls, value):
        """将逗号分隔的 CORS 配置转换为列表。"""
        if isinstance(value, str) and value != "*":
            if "," in value:
                # 注意：允许以逗号分隔配置多个来源
                return [origin.strip() for origin in value.split(",")]
            # 注意：单个来源也统一成列表，避免下游分支判断
            return [value]
        return value

    @field_validator("use_noop_database", mode="before")
    @classmethod
    def set_use_noop_database(cls, value):
        if value:
            logger.info("Running with NOOP database session. All DB operations are disabled.")
        return value

    @field_validator("event_delivery", mode="before")
    @classmethod
    def set_event_delivery(cls, value, info):
        # 注意：多 worker 环境不支持 polling/streaming，需强制 direct
        if info.data.get("workers", 1) > 1:
            logger.warning("Multi-worker environment detected, using direct event delivery")
            return "direct"
        return value

    @field_validator("user_agent", mode="after")
    @classmethod
    def set_user_agent(cls, value):
        if not value:
            value = "Langflow"
        import os

        os.environ["USER_AGENT"] = value
        logger.debug(f"Setting user agent to {value}")
        return value

    @field_validator("mcp_composer_version", mode="before")
    @classmethod
    def validate_mcp_composer_version(cls, value):
        """规范化 mcp-composer 版本字符串为带前缀的 PEP 440 规范。"""
        if not value:
            return "==0.1.0.8.10"  # 默认值

        # 注意：先匹配更长的前缀，避免误判
        specifiers = ["===", "==", "!=", "<=", ">=", "~=", "<", ">"]
        if any(value.startswith(spec) for spec in specifiers):
            return value

        # 注意：裸版本号补上 ~= 前缀，允许补丁版本更新
        import re

        if re.match(r"^\d+(\.\d+)*", value):
            logger.debug(f"Adding ~= prefix to bare version '{value}' -> '~={value}'")
            return f"~={value}"

        # 注意：无法判定时保持原样，交由 uvx 处理
        return value

    @field_validator("variables_to_get_from_environment", mode="before")
    @classmethod
    def set_variables_to_get_from_environment(cls, value):
        import os

        if isinstance(value, str):
            value = value.split(",")

        result = list(set(VARIABLES_TO_GET_FROM_ENVIRONMENT + value))

        # 注意：校验器无法访问实例属性，直接读取环境变量判断
        if os.getenv("LANGFLOW_AGENTIC_EXPERIENCE", "true").lower() == "true":
            result.extend(AGENTIC_VARIABLES)

        return list(set(result))

    @field_validator("log_file", mode="before")
    @classmethod
    def set_log_file(cls, value):
        if isinstance(value, Path):
            value = str(value)
        return value

    @field_validator("config_dir", mode="before")
    @classmethod
    def set_langflow_dir(cls, value):
        if not value:
            from platformdirs import user_cache_dir

            # 注意：统一应用名称与作者，确保跨平台路径一致
            app_name = "langflow"
            app_author = "langflow"

            # 注意：使用缓存目录承载配置，避免污染工作目录
            cache_dir = user_cache_dir(app_name, app_author)

            # 注意：确保目录存在
            value = Path(cache_dir)
            value.mkdir(parents=True, exist_ok=True)

        if isinstance(value, str):
            value = Path(value)
        # 注意：转为绝对路径，规避相对路径造成的歧义
        value = value.resolve()
        if not value.exists():
            value.mkdir(parents=True, exist_ok=True)

        return str(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def set_database_url(cls, value, info):
        """解析并生成最终数据库 URL。

        关键路径：
        1) 若环境变量 `LANGFLOW_DATABASE_URL` 存在则优先使用
        2) 根据版本与配置确定数据库落盘路径
        3) 生成 `sqlite:///` 形式的 URL

        异常流：无效 URL 或 `config_dir` 缺失会抛 ValueError。
        排障入口：日志关键字 `database`/`LANGFLOW_DATABASE_URL`。
        """
        if value and not is_valid_database_url(value):
            msg = f"Invalid database_url provided: '{value}'"
            raise ValueError(msg)

        if langflow_database_url := os.getenv("LANGFLOW_DATABASE_URL"):
            value = langflow_database_url
            logger.debug("Using LANGFLOW_DATABASE_URL env variable")
        else:
            # 注意：兼容旧路径 sqlite:///./langflow.db 的迁移逻辑
            if not info.data["config_dir"]:
                msg = "config_dir not set, please set it or provide a database_url"
                raise ValueError(msg)

            from lfx.utils.version import get_version_info
            from lfx.utils.version import is_pre_release as langflow_is_pre_release

            version = get_version_info()["version"]
            is_pre_release = langflow_is_pre_release(version)

            if info.data["save_db_in_config_dir"]:
                database_dir = info.data["config_dir"]
            else:
                # 注意：为兼容历史包路径，优先使用 langflow 包目录
                try:
                    import langflow

                    database_dir = Path(langflow.__file__).parent.resolve()
                except ImportError:
                    database_dir = Path(__file__).parent.parent.parent.resolve()

            pre_db_file_name = "langflow-pre.db"
            db_file_name = "langflow.db"
            new_pre_path = f"{database_dir}/{pre_db_file_name}"
            new_path = f"{database_dir}/{db_file_name}"
            final_path = None
            if is_pre_release:
                if Path(new_pre_path).exists():
                    final_path = new_pre_path
                elif Path(new_path).exists() and info.data["save_db_in_config_dir"]:
                    # 注意：预发布版本需要复制现有 DB 到新位置
                    logger.debug("Copying existing database to new location")
                    copy2(new_path, new_pre_path)
                    logger.debug(f"Copied existing database to {new_pre_path}")
                elif Path(f"./{db_file_name}").exists() and info.data["save_db_in_config_dir"]:
                    logger.debug("Copying existing database to new location")
                    copy2(f"./{db_file_name}", new_pre_path)
                    logger.debug(f"Copied existing database to {new_pre_path}")
                else:
                    logger.debug(f"Creating new database at {new_pre_path}")
                    final_path = new_pre_path
            elif Path(new_path).exists():
                final_path = new_path
            elif Path(f"./{db_file_name}").exists():
                try:
                    logger.debug("Copying existing database to new location")
                    copy2(f"./{db_file_name}", new_path)
                    logger.debug(f"Copied existing database to {new_path}")
                except OSError:
                    logger.exception("Failed to copy database, using default path")
                    new_path = f"./{db_file_name}"
            else:
                final_path = new_path

            if final_path is None:
                final_path = new_pre_path if is_pre_release else new_path

            value = f"sqlite:///{final_path}"

        return value

    @field_validator("components_path", mode="before")
    @classmethod
    def set_components_path(cls, value):
        """合并组件路径列表，并注入环境变量覆盖项。

        关键路径：
        1) 读取 `LANGFLOW_COMPONENTS_PATH` 并去重追加
        2) 列表为空时回退 `BASE_COMPONENTS_PATH`
        3) 统一输出为字符串列表

        异常流：环境变量路径不存在时忽略，不抛异常。
        """
        if os.getenv("LANGFLOW_COMPONENTS_PATH"):
            logger.debug("Adding LANGFLOW_COMPONENTS_PATH to components_path")
            langflow_component_path = os.getenv("LANGFLOW_COMPONENTS_PATH")
            if Path(langflow_component_path).exists() and langflow_component_path not in value:
                if isinstance(langflow_component_path, list):
                    for path in langflow_component_path:
                        if path not in value:
                            value.append(path)
                    logger.debug(f"Extending {langflow_component_path} to components_path")
                elif langflow_component_path not in value:
                    value.append(langflow_component_path)
                    logger.debug(f"Appending {langflow_component_path} to components_path")

        if not value:
            value = [BASE_COMPONENTS_PATH]
        elif isinstance(value, Path):
            value = [str(value)]
        elif isinstance(value, list):
            value = [str(p) if isinstance(p, Path) else p for p in value]
        return value

    model_config = SettingsConfigDict(validate_assignment=True, extra="ignore", env_prefix="LANGFLOW_")

    async def update_from_yaml(self, file_path: str, *, dev: bool = False) -> None:
        """从 YAML 文件加载设置并覆盖当前实例。"""
        new_settings = await load_settings_from_yaml(file_path)
        self.components_path = new_settings.components_path or []
        self.dev = dev

    def update_settings(self, **kwargs) -> None:
        """按键批量更新设置，支持列表合并。

        关键路径：
        1) 忽略未知键，避免污染配置
        2) 列表字段支持 JSON 字符串解析与去重追加
        3) 非列表字段直接覆盖

        异常流：JSON 解析失败会被吞并，不影响其他字段更新。
        """
        for key, value in kwargs.items():
            # 注意：value 可能包含敏感信息，避免日志泄露
            if not hasattr(self, key):
                continue
            if isinstance(getattr(self, key), list):
                # 注意：支持将字符串形式的列表解析为实际列表
                value_ = value
                with contextlib.suppress(json.decoder.JSONDecodeError):
                    value_ = orjson.loads(str(value))
                if isinstance(value_, list):
                    for item in value_:
                        item_ = str(item) if isinstance(item, Path) else item
                        if item_ not in getattr(self, key):
                            getattr(self, key).append(item_)
                else:
                    value_ = str(value_) if isinstance(value_, Path) else value_
                    if value_ not in getattr(self, key):
                        getattr(self, key).append(value_)
            else:
                setattr(self, key, value)

    @property
    def voice_mode_available(self) -> bool:
        """通过尝试导入 `webrtcvad` 判断语音模式是否可用。"""
        try:
            import webrtcvad  # noqa: F401
        except ImportError:
            return False
        else:
            return True

    @classmethod
    @override
    def settings_customise_sources(  # type: ignore[misc]
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (CustomSource(settings_cls),)


def save_settings_to_yaml(settings: Settings, file_path: str) -> None:
    """将 Settings 序列化为 YAML 文件。"""
    with Path(file_path).open("w", encoding="utf-8") as f:
        settings_dict = settings.model_dump()
        yaml.dump(settings_dict, f)


async def load_settings_from_yaml(file_path: str) -> Settings:
    """从 YAML 文件加载 Settings。

    关键路径：
    1) 解析相对路径到当前目录
    2) 读取并安全解析 YAML
    3) 转换为大写键并构建 Settings

    异常流：未知键会抛 KeyError。
    排障入口：日志关键字 `Loading`。
    """
    # 注意：支持仅传文件名，默认与当前模块同目录
    if "/" not in file_path:
        # 获取当前模块路径
        current_path = Path(__file__).resolve().parent
        file_path_ = Path(current_path) / file_path
    else:
        file_path_ = Path(file_path)

    async with async_open(file_path_.name, encoding="utf-8") as f:
        content = await f.read()
        settings_dict = yaml.safe_load(content)
        settings_dict = {k.upper(): v for k, v in settings_dict.items()}

        for key in settings_dict:
            if key not in Settings.model_fields:
                msg = f"Key {key} not found in settings"
                raise KeyError(msg)
            await logger.adebug(f"Loading {len(settings_dict[key])} {key} from {file_path}")

    return await asyncio.to_thread(Settings, **settings_dict)
