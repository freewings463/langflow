"""
模块名称：节点构建日志数据访问

本模块提供节点构建记录的查询、写入与清理逻辑。
主要功能包括：按流程查询最新构建、记录构建并控制保留数量。

关键组件：`get_vertex_builds_by_flow_id` / `log_vertex_build`
设计背景：集中管理构建记录的保留策略，避免表无限增长。
使用场景：流程执行调试与构建记录展示。
注意事项：写入时会裁剪超限记录。
"""

from uuid import UUID

from sqlmodel import col, delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.database.models.vertex_builds.model import VertexBuildBase, VertexBuildTable
from langflow.services.deps import get_settings_service


async def get_vertex_builds_by_flow_id(
    db: AsyncSession, flow_id: UUID, limit: int | None = 1000
) -> list[VertexBuildTable]:
    """按流程 `ID` 获取最新构建记录列表。

    契约：
    - 输入：`db`、`flow_id` 与 `limit`。
    - 输出：`VertexBuildTable` 列表。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    关键路径：
    1) 使用子查询获取每个 `id` 的最新时间戳。
    2) 关联主查询并按时间排序返回。

    决策：对子查询按 `id` 分组取最大时间戳。
    问题：同一节点存在多个构建记录。
    方案：只返回每个 `id` 最新记录。
    代价：历史构建需另行查询。
    重评：当需要完整历史时提供分页接口。
    """
    if isinstance(flow_id, str):
        flow_id = UUID(flow_id)
    subquery = (
        select(VertexBuildTable.id, func.max(VertexBuildTable.timestamp).label("max_timestamp"))
        .where(VertexBuildTable.flow_id == flow_id)
        .group_by(VertexBuildTable.id)
        .subquery()
    )
    stmt = (
        select(VertexBuildTable)
        .join(
            subquery, (VertexBuildTable.id == subquery.c.id) & (VertexBuildTable.timestamp == subquery.c.max_timestamp)
        )
        .where(VertexBuildTable.flow_id == flow_id)
        .order_by(col(VertexBuildTable.timestamp))
        .limit(limit)
    )

    builds = await db.exec(stmt)
    return list(builds)


async def log_vertex_build(
    db: AsyncSession,
    vertex_build: VertexBuildBase,
    *,
    max_builds_to_keep: int | None = None,
    max_builds_per_vertex: int | None = None,
) -> VertexBuildTable:
    """记录节点构建并维护保留策略。

    契约：
    - 输入：`db`、`vertex_build`，以及可选 `max_builds_to_keep`/`max_builds_per_vertex`。
    - 输出：新建 `VertexBuildTable`。
    - 副作用：写入数据库并删除超限记录。
    - 失败语义：异常时回滚并抛出。

    关键路径（三步）：
    1) 插入新构建记录并 `flush`。
    2) 删除单节点超限记录。
    3) 删除全局超限记录并提交。

    决策：在同一事务中完成插入与裁剪。
    问题：构建记录增长过快会影响存储。
    方案：按配置裁剪并统一提交。
    代价：历史记录被裁剪。
    重评：当引入归档或冷存储时调整裁剪策略。
    """
    table = VertexBuildTable(**vertex_build.model_dump())

    try:
        settings = get_settings_service().settings
        max_global = max_builds_to_keep or settings.max_vertex_builds_to_keep
        max_per_vertex = max_builds_per_vertex or settings.max_vertex_builds_per_vertex

        # 注意：先插入并 `flush` 以便后续查询可见。
        db.add(table)
        await db.flush()

        # 注意：裁剪单节点记录，保留最新 `max_per_vertex` 条。
        keep_vertex_subq = (
            select(VertexBuildTable.build_id)
            .where(
                VertexBuildTable.flow_id == vertex_build.flow_id,
                VertexBuildTable.id == vertex_build.id,
            )
            .order_by(col(VertexBuildTable.timestamp).desc(), col(VertexBuildTable.build_id).desc())
            .limit(max_per_vertex)
        )
        delete_vertex_older = delete(VertexBuildTable).where(
            VertexBuildTable.flow_id == vertex_build.flow_id,
            VertexBuildTable.id == vertex_build.id,
            col(VertexBuildTable.build_id).not_in(keep_vertex_subq),
        )
        await db.exec(delete_vertex_older)

        # 注意：裁剪全局记录，保留最新 `max_global` 条。
        keep_global_subq = (
            select(VertexBuildTable.build_id)
            .order_by(col(VertexBuildTable.timestamp).desc(), col(VertexBuildTable.build_id).desc())
            .limit(max_global)
        )
        delete_global_older = delete(VertexBuildTable).where(col(VertexBuildTable.build_id).not_in(keep_global_subq))
        await db.exec(delete_global_older)

        # 注意：提交事务。
        await db.commit()

    except Exception:
        await db.rollback()
        raise

    return table


async def delete_vertex_builds_by_flow_id(db: AsyncSession, flow_id: UUID) -> None:
    """删除指定流程的所有构建记录。

    契约：
    - 输入：`db` 与 `flow_id`。
    - 输出：`None`。
    - 副作用：删除数据库记录（提交由调用方负责）。
    - 失败语义：删除异常透传。

    决策：保持与调用方事务一致，由调用方决定提交。
    问题：清理操作可能与其他写入共享事务。
    方案：仅执行删除，不强制提交。
    代价：调用方需显式提交。
    重评：当该函数独立使用时可加入可选提交参数。
    """
    stmt = delete(VertexBuildTable).where(VertexBuildTable.flow_id == flow_id)
    await db.exec(stmt)
