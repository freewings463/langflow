"""
模块名称：`SQL` 执行组件

本模块提供 `SQLAlchemy` 兼容数据库的查询执行能力，并输出 `Message` 或 `DataFrame`。
主要功能包括：
- 连接数据库并复用缓存连接
- 执行 `SQL` 查询并返回结果

关键组件：
- `SQLComponent`

设计背景：统一数据库查询入口以便组件化使用。
注意事项：连接失败或查询错误会抛 `ValueError`，可通过日志排障。
"""

from typing import TYPE_CHECKING, Any

from langchain_community.utilities import SQLDatabase
from sqlalchemy.exc import SQLAlchemyError

from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.io import BoolInput, MessageTextInput, MultilineInput, Output
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.services.cache.utils import CacheMiss

if TYPE_CHECKING:
    from sqlalchemy.engine import Result


class SQLComponent(ComponentWithCache):
    """`SQL` 数据库查询组件

    契约：
    - 输入：数据库连接串与查询语句
    - 输出：`Message` 或 `DataFrame`
    - 副作用：可能建立数据库连接并写入缓存
    - 失败语义：连接或查询失败时抛 `ValueError`
    """

    display_name = "SQL Database"
    description = "Executes SQL queries on SQLAlchemy-compatible databases."
    documentation: str = "https://docs.langflow.org/sql-database"
    icon = "database"
    name = "SQLComponent"
    metadata = {"keywords": ["sql", "database", "query", "db", "fetch"]}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db: SQLDatabase = None

    def maybe_create_db(self):
        """按需创建数据库连接并缓存

        契约：
        - 输入：无
        - 输出：无
        - 副作用：初始化 `self.db` 并更新缓存
        - 失败语义：连接失败时抛 `ValueError`
        """
        if self.database_url != "":
            if self._shared_component_cache:
                cached_db = self._shared_component_cache.get(self.database_url)
                if not isinstance(cached_db, CacheMiss):
                    self.db = cached_db
                    return
                self.log("Connecting to database")
            try:
                self.db = SQLDatabase.from_uri(self.database_url)
            except Exception as e:
                msg = f"An error occurred while connecting to the database: {e}"
                raise ValueError(msg) from e
            if self._shared_component_cache:
                self._shared_component_cache.set(self.database_url, self.db)

    inputs = [
        MessageTextInput(name="database_url", display_name="Database URL", required=True),
        MultilineInput(name="query", display_name="SQL Query", tool_mode=True, required=True),
        BoolInput(name="include_columns", display_name="Include Columns", value=True, tool_mode=True, advanced=True),
        BoolInput(
            name="add_error",
            display_name="Add Error",
            value=False,
            tool_mode=True,
            info="If True, the error will be added to the result",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Result Table", name="run_sql_query", method="run_sql_query"),
    ]

    def build_component(
        self,
    ) -> Message:
        """执行查询并返回文本结果

        契约：
        - 输入：无（使用组件字段）
        - 输出：`Message`
        - 副作用：更新 `self.status`
        - 失败语义：查询失败时返回错误信息
        """
        error = None
        self.maybe_create_db()
        try:
            result = self.db.run(self.query, include_columns=self.include_columns)
            self.status = result
        except SQLAlchemyError as e:
            msg = f"An error occurred while running the SQL Query: {e}"
            self.log(msg)
            result = str(e)
            self.status = result
            error = repr(e)

        if self.add_error and error is not None:
            result = f"{result}\n\nError: {error}\n\nQuery: {self.query}"
        elif error is not None:
            # 注意：不追加错误信息时仅返回原查询
            result = self.query

        return Message(text=result)

    def __execute_query(self) -> list[dict[str, Any]]:
        """执行查询并返回行字典列表

        契约：
        - 输入：无（使用组件字段）
        - 输出：字典列表
        - 副作用：可能建立数据库连接
        - 失败语义：查询失败时抛 `ValueError`
        """
        self.maybe_create_db()
        try:
            cursor: Result[Any] = self.db.run(self.query, fetch="cursor")
            return [x._asdict() for x in cursor.fetchall()]
        except SQLAlchemyError as e:
            msg = f"An error occurred while running the SQL Query: {e}"
            self.log(msg)
            raise ValueError(msg) from e

    def run_sql_query(self) -> DataFrame:
        """执行查询并返回 `DataFrame`

        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame`
        - 副作用：更新 `self.status`
        - 失败语义：查询失败时抛 `ValueError`
        """
        result = self.__execute_query()
        df_result = DataFrame(result)
        self.status = df_result
        return df_result
