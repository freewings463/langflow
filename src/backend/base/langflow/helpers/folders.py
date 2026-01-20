"""
模块名称：文件夹名称辅助

本模块提供文件夹命名冲突的处理工具。
主要功能包括：
- 根据用户生成唯一文件夹名称

关键组件：
- `generate_unique_folder_name`

设计背景：同一用户下文件夹名称需要唯一显示。
注意事项：名称冲突时会追加递增后缀。
"""

from sqlalchemy import select

from langflow.services.database.models.folder.model import Folder


async def generate_unique_folder_name(folder_name, user_id, session):
    """生成不与现有文件夹重名的名称。

    契约：冲突时追加 ` (n)`，直到唯一。
    失败语义：DB 查询失败向上抛异常。

    决策：采用递增后缀 `(n)` 避免冲突
    问题：用户输入名称可能重复
    方案：循环查询并递增
    代价：高冲突场景会增加查询次数
    重评：若启用唯一索引与重试可优化
    """
    original_name = folder_name
    n = 1
    while True:
        existing_folder = (
            await session.exec(
                select(Folder).where(
                    Folder.name == folder_name,
                    Folder.user_id == user_id,
                )
            )
        ).first()

        if not existing_folder:
            return folder_name

        folder_name = f"{original_name} ({n})"
        n += 1
