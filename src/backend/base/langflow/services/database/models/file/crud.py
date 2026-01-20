"""
模块名称：文件模型查询

本模块提供文件元数据的查询方法。
主要功能包括：按 `file_id` 获取 `File` 记录。

关键组件：`get_file_by_id`
设计背景：统一文件查询入口，减少重复 SQL 构造。
使用场景：文件下载、权限校验与元信息展示。
注意事项：入参可为 `UUID` 或字符串，内部会统一转换。
"""

from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.database.models.file.model import File


async def get_file_by_id(db: AsyncSession, file_id: UUID) -> File | None:
    """按文件 `ID` 获取 `File` 记录。

    契约：
    - 输入：`db` 会话与 `file_id`。
    - 输出：匹配到则返回 `File`，否则 `None`。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    关键路径：
    1) 将字符串 `file_id` 转为 `UUID`。
    2) 构造查询并返回首条结果。

    决策：在函数内进行 `UUID` 规范化。
    问题：调用方可能传入字符串形式的 `UUID`。
    方案：检测类型并转换。
    代价：非 `UUID` 字符串将触发解析异常。
    重评：当上游统一类型后可移除转换。
    """
    if isinstance(file_id, str):
        file_id = UUID(file_id)
    stmt = select(File).where(File.id == file_id)

    return (await db.exec(stmt)).first()
