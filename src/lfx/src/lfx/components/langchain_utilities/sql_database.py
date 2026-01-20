"""模块名称：SQLDatabase 组件

本模块封装 LangChain `SQLDatabase` 创建逻辑，支持将连接 URI 规范化并构建 SQLAlchemy 引擎。
主要功能包括：清洗 URI、创建 `StaticPool` 引擎、返回 `SQLDatabase` 实例。

关键组件：
- `SQLDatabaseComponent`：SQL 数据库组件入口

设计背景：在组件层统一数据库连接创建方式。
注意事项：`postgres://` 会被替换为 `postgresql://` 以兼容 SQLAlchemy。
"""

from langchain_community.utilities.sql_database import SQLDatabase
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from lfx.custom.custom_component.component import Component
from lfx.io import (
    Output,
    StrInput,
)


class SQLDatabaseComponent(Component):
    """SQLDatabase 组件。

    契约：输入 `uri`；输出 `SQLDatabase`；副作用：创建数据库引擎；
    失败语义：URI 不合法会由 SQLAlchemy 抛异常。
    关键路径：1) 清洗 URI 2) 创建引擎 3) 包装为 `SQLDatabase`。
    决策：使用 `StaticPool`
    问题：在短连接/轻量场景避免连接池开销
    方案：固定连接池类型
    代价：并发连接扩展性有限
    重评：当高并发访问时改用默认池或可配置池
    """
    display_name = "SQLDatabase"
    description = "SQL Database"
    name = "SQLDatabase"
    icon = "LangChain"

    inputs = [
        StrInput(name="uri", display_name="URI", info="URI to the database.", required=True),
    ]

    outputs = [
        Output(display_name="SQLDatabase", name="SQLDatabase", method="build_sqldatabase"),
    ]

    def clean_up_uri(self, uri: str) -> str:
        """规范化数据库 URI。

        契约：输入 `uri` 字符串；输出规范化 URI；副作用无；
        失败语义：无。
        关键路径：1) 替换 `postgres://` 前缀 2) 去除首尾空白。
        决策：统一 `postgresql://` 前缀
        问题：SQLAlchemy 不接受 `postgres://`
        方案：字符串替换
        代价：对非 PostgreSQL URI 无影响
        重评：当驱动支持 `postgres://` 时取消替换
        """
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://")
        return uri.strip()

    def build_sqldatabase(self) -> SQLDatabase:
        """构建 `SQLDatabase` 实例。

        契约：输入 `uri`；输出 `SQLDatabase`；副作用：创建引擎；
        失败语义：连接错误抛异常。
        关键路径：1) 清洗 URI 2) 创建引擎 3) 包装返回。
        决策：引擎使用 `StaticPool`
        问题：组件内不维护长连接池配置
        方案：固定池类型简化配置
        代价：高并发场景性能受限
        重评：当需要连接池配置时开放参数
        """
        uri = self.clean_up_uri(self.uri)
        # 使用 SQLAlchemy 创建 `StaticPool` 引擎
        engine = create_engine(uri, poolclass=StaticPool)
        return SQLDatabase(engine)
