"""
模块名称：无服务端流程执行器

本模块提供 `LangflowRunnerExperimental`，用于在无独立服务端的环境中执行流程。
主要功能：
- 解析流程定义并写入数据库
- 构建图并运行流程
- 执行后清理缓存与用户/流程状态
设计背景：面向脚本化或嵌入式场景的轻量执行入口。
注意事项：该类为实验性质，接口可能变更；默认会清理流程与用户状态。
"""

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from aiofile import async_open
from lfx.graph import Graph
from lfx.graph.vertex.param_handler import ParameterHandler
from lfx.log.logger import configure, logger
from lfx.utils.util import update_settings
from sqlmodel import delete, select, text

from langflow.api.utils import cascade_delete_flow
from langflow.load.utils import replace_tweaks_with_env
from langflow.processing.process import process_tweaks, run_graph
from langflow.services.auth.utils import get_password_hash
from langflow.services.cache.service import AsyncBaseCacheService
from langflow.services.database.models import Flow, User, Variable
from langflow.services.database.utils import initialize_database
from langflow.services.deps import get_cache_service, get_storage_service, session_scope


class LangflowRunnerExperimental:
    """无服务端流程执行器（实验性）。

    契约：
    - 输入：流程定义（路径/字典）、输入值、会话标识
    - 输出：流程运行结果（可能为流式）
    - 副作用：写入数据库与缓存；可选清理用户与流程状态
    - 失败语义：流程解析/执行失败会抛异常给调用方

    关键路径（三步）：
    1) 初始化数据库与配置，并准备流程数据
    2) 构建图并执行流程
    3) 按需清理流程与用户状态

    注意：本类为实验阶段，接口与行为可能调整。
    """

    def __init__(
        self,
        *,
        should_initialize_db: bool = True,
        log_level: str | None = None,
        log_file: str | None = None,
        log_rotation: str | None = None,
        disable_logs: bool = False,
    ):
        """初始化执行器并配置日志输出。

        契约：
        - 输入：日志级别、文件与滚动策略
        - 副作用：调用 `configure` 配置全局日志
        """
        self.should_initialize_db = should_initialize_db
        log_file_path = Path(log_file) if log_file else None
        configure(
            log_level=log_level,
            log_file=log_file_path,
            log_rotation=log_rotation,
            disable=disable_logs,
        )

    async def run(
        self,
        session_id: str,  # 注意：当前要求 UUID 字符串。
        flow: Path | str | dict,
        input_value: str,
        *,
        input_type: str = "chat",
        output_type: str = "all",
        cache: str | None = None,
        stream: bool = False,
        user_id: str | None = None,
        generate_user: bool = False,  # 注意：为流程生成新用户。
        cleanup: bool = True,  # 注意：执行后清理流程与用户状态。
        tweaks_values: dict | None = None,
    ):
        """执行流程并返回结果。

        契约：
        - 输入：`flow`、`input_value`、`session_id`
        - 输出：运行结果或流式响应
        - 副作用：可能创建用户、写入数据库、更新缓存
        - 失败语义：异常直接抛出；若 `cleanup=True` 将尝试清理

        关键路径（三步）：
        1) 初始化数据库与配置
        2) 准备流程并写入数据库
        3) 执行流程并可选清理状态
        """
        try:
            await logger.ainfo(f"Start Handling {session_id=}")
            await self.init_db_if_needed()
            # 实现：更新缓存与组件路径配置。
            await update_settings(cache=cache)
            if generate_user:
                user = await self.generate_user()
                user_id = str(user.id)
            flow_dict = await self.prepare_flow_and_add_to_db(
                flow=flow,
                user_id=user_id,
                session_id=session_id,
                tweaks_values=tweaks_values,
            )
            return await self.run_flow(
                input_value=input_value,
                session_id=session_id,
                flow_dict=flow_dict,
                input_type=input_type,
                output_type=output_type,
                user_id=user_id,
                stream=stream,
            )
        finally:
            if cleanup and user_id:
                await self.clear_user_state(user_id=user_id)

    async def run_flow(
        self,
        *,
        input_value: str,
        session_id: str,
        flow_dict: dict,
        input_type: str = "chat",
        output_type: str = "all",
        user_id: str | None = None,
        stream: bool = False,
    ):
        """执行已准备的流程字典。

        契约：
        - 输入：`flow_dict` 与输入参数
        - 输出：运行结果
        - 副作用：构建图并运行；清理流程状态
        - 失败语义：异常抛出并在 `finally` 中清理
        """
        graph = await self.create_graph_from_flow(session_id, flow_dict, user_id=user_id)
        try:
            result = await self.run_graph(input_value, input_type, output_type, session_id, graph, stream=stream)
        finally:
            await self.clear_flow_state(flow_dict)
        await logger.ainfo(f"Finish Handling {session_id=}")
        return result

    async def prepare_flow_and_add_to_db(
        self,
        *,
        flow: Path | str | dict,
        user_id: str | None = None,
        custom_flow_id: str | None = None,
        session_id: str | None = None,
        tweaks_values: dict | None = None,
    ) -> dict:
        """解析流程并写入数据库。

        契约：
        - 输入：流程定义与可选 `custom_flow_id`
        - 输出：标准化后的 `flow_dict`
        - 副作用：清理旧流程状态并写入数据库
        """
        flow_dict = await self.get_flow_dict(flow)
        session_id = session_id or custom_flow_id or str(uuid4())
        if custom_flow_id:
            flow_dict["id"] = custom_flow_id
        flow_dict = self.process_tweaks(flow_dict, tweaks_values=tweaks_values)
        await self.clear_flow_state(flow_dict)
        await self.add_flow_to_db(flow_dict, user_id=user_id)
        return flow_dict

    def process_tweaks(self, flow_dict: dict, tweaks_values: dict | None = None) -> dict:
        """解析并应用 `tweaks`，并清理 `load_from_db` 标记。

        契约：
        - 输入：流程字典与可选环境变量覆盖
        - 输出：处理后的流程字典
        - 副作用：可能读取环境变量与存储服务
        """
        tweaks: dict | None = None
        tweaks_values = tweaks_values or os.environ.copy()
        for vertex in Graph.from_payload(flow_dict).vertices:
            param_handler = ParameterHandler(vertex, get_storage_service())
            field_params, load_from_db_fields = param_handler.process_field_parameters()
            for db_field in load_from_db_fields:
                if field_params[db_field]:
                    tweaks = tweaks or {}
                    tweaks[vertex.id] = tweaks.get(vertex.id, {})
                    tweaks[vertex.id][db_field] = field_params[db_field]
        if tweaks is not None:
            tweaks = replace_tweaks_with_env(tweaks=tweaks, env_vars=tweaks_values)
            flow_dict = process_tweaks(flow_dict, tweaks)

        # 实现：递归清理 `load_from_db=True`，避免运行时再次加载。
        def update_load_from_db(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "load_from_db" and value is True:
                        obj[key] = False
                    else:
                        update_load_from_db(value)
            elif isinstance(obj, list):
                for item in obj:
                    update_load_from_db(item)

        update_load_from_db(flow_dict)
        return flow_dict

    async def generate_user(self) -> User:
        """生成临时用户并写入数据库。"""
        async with session_scope() as session:
            user_id = str(uuid4())
            user = User(id=user_id, username=user_id, password=get_password_hash(str(uuid4())), is_active=True)
            session.add(user)
            await session.flush()
            await session.refresh(user)
            return user

    @staticmethod
    async def add_flow_to_db(flow_dict: dict, user_id: str | None):
        """将流程写入数据库。"""
        async with session_scope() as session:
            flow_db = Flow(
                name=flow_dict.get("name"), id=UUID(flow_dict["id"]), data=flow_dict.get("data", {}), user_id=user_id
            )
            session.add(flow_db)

    @staticmethod
    async def run_graph(
        input_value: str,
        input_type: str,
        output_type: str,
        session_id: str,
        graph: Graph,
        *,
        stream: bool,
    ):
        """运行图并返回结果。"""
        return await run_graph(
            graph=graph,
            session_id=session_id,
            input_value=input_value,
            fallback_to_env_vars=True,
            input_type=input_type,
            output_type=output_type,
            stream=stream,
        )

    @staticmethod
    async def create_graph_from_flow(session_id: str, flow_dict: dict, user_id: str | None = None):
        """根据流程字典创建图并初始化运行上下文。"""
        graph = Graph.from_payload(
            payload=flow_dict, flow_id=flow_dict["id"], flow_name=flow_dict.get("name"), user_id=user_id
        )
        graph.session_id = session_id
        graph.set_run_id(session_id)
        graph.user_id = user_id
        await graph.initialize_run()
        return graph

    @staticmethod
    async def clear_flow_state(flow_dict: dict):
        """清理流程状态与缓存。

        契约：
        - 输入：`flow_dict`
        - 副作用：清理缓存并删除数据库中的流程记录
        """
        cache_service = get_cache_service()
        if isinstance(cache_service, AsyncBaseCacheService):
            await cache_service.clear()
        else:
            cache_service.clear()
        async with session_scope() as session:
            flow_id = flow_dict["id"]
            uuid_obj = flow_id if isinstance(flow_id, UUID) else UUID(str(flow_id))
            await cascade_delete_flow(session, uuid_obj)

    @staticmethod
    async def clear_user_state(user_id: str):
        """清理用户相关流程与变量数据。"""
        async with session_scope() as session:
            flows = await session.exec(select(Flow.id).where(Flow.user_id == user_id))
            flow_ids: list[UUID] = [fid for fid in flows.scalars().all() if fid is not None]
            for flow_id in flow_ids:
                await cascade_delete_flow(session, flow_id)
            await session.exec(delete(Variable).where(Variable.user_id == user_id))
            await session.exec(delete(User).where(User.id == user_id))

    async def init_db_if_needed(self):
        """按需初始化数据库。"""
        if not await self.database_exists_check() and self.should_initialize_db:
            await logger.ainfo("Initializing database...")
            await initialize_database(fix_migration=True)
            self.should_initialize_db = False
            await logger.ainfo("Database initialized.")

    @staticmethod
    async def database_exists_check():
        """检查数据库迁移表是否存在。"""
        async with session_scope() as session:
            try:
                result = await session.exec(text("SELECT version_num FROM public.alembic_version"))
                return result.first() is not None
            except Exception as e:  # noqa: BLE001
                await logger.adebug(f"Database check failed: {e}")
                return False

    @staticmethod
    async def get_flow_dict(flow: Path | str | dict) -> dict:
        """将流程输入规范化为字典。

        契约：
        - 输入：路径/字典
        - 输出：流程字典
        - 失败语义：类型不支持时抛 `TypeError`
        """
        if isinstance(flow, str | Path):
            async with async_open(Path(flow), encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        elif isinstance(flow, dict):
            return flow
        error_msg = "Input must be a file path (str or Path object) or a JSON object (dict)."
        raise TypeError(error_msg)
