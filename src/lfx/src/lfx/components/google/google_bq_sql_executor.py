"""
模块名称：`BigQuery` `SQL` 执行组件

本模块提供 `BigQueryExecutorComponent`，用于执行 `BigQuery` `SQL` 并返回 `DataFrame`。
主要功能包括：
- 读取服务账号 `JSON` 并构建凭证
- 清洗 `SQL` 文本并执行查询
- 返回结构化查询结果

关键组件：`BigQueryExecutorComponent`
设计背景：为 `BigQuery` 查询提供统一的组件封装
注意事项：需提供有效服务账号 `JSON`；空查询直接失败
"""

import json
import re
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from lfx.custom import Component
from lfx.io import BoolInput, FileInput, MessageTextInput, Output
from lfx.schema.dataframe import DataFrame


class BigQueryExecutorComponent(Component):
    """`BigQuery` `SQL` 执行组件。
    契约：输入为服务账号 `JSON` 与 `SQL`；输出为 `DataFrame`。
    关键路径：读取凭证 → 规范化 `SQL` → 执行查询 → 返回结果。
    决策：默认允许自动清洗 `SQL`。问题：用户输入常包含代码块/引号；方案：清洗逻辑；代价：可能改变原始语句；重评：当需要严格保留原 `SQL` 时。
    """

    display_name = "BigQuery"
    description = "Execute SQL queries on Google BigQuery."
    name = "BigQueryExecutor"
    icon = "Google"
    beta: bool = True

    inputs = [
        FileInput(
            name="service_account_json_file",
            display_name="Upload Service Account JSON",
            info="Upload the JSON file containing Google Cloud service account credentials.",
            file_types=["json"],
            required=True,
        ),
        MessageTextInput(
            name="query",
            display_name="SQL Query",
            info="The SQL query to execute on BigQuery.",
            required=True,
            tool_mode=True,
        ),
        BoolInput(
            name="clean_query",
            display_name="Clean Query",
            info="When enabled, this will automatically clean up your SQL query.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Query Results", name="query_results", method="execute_sql"),
    ]

    def _clean_sql_query(self, query: str) -> str:
        """清洗 `SQL` 查询文本。
        契约：返回去除代码块/引号/无关内容后的 `SQL`。
        关键路径：提取代码块 → 关键词截取 → 去引号/反引号 → 正则清理。
        决策：优先解析代码块。问题：用户常粘贴 `Markdown`；方案：正则提取；代价：多段 `SQL` 仅取首段；重评：当需要多语句支持时。
        """
        # 注意：优先从代码块中提取 `SQL`。
        sql_pattern = r"```(?:sql)?\s*([\s\S]*?)\s*```"
        sql_matches = re.findall(sql_pattern, query, re.IGNORECASE)

        if sql_matches:
            # 注意：存在代码块时使用首段 `SQL`。
            query = sql_matches[0]
        else:
            # 注意：无代码块时，尝试按 `SQL` 关键词截取。
            sql_keywords = r"(?i)(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|MERGE)"
            lines = query.split("\n")
            sql_lines = []
            in_sql = False

            for _line in lines:
                line = _line.strip()
                if re.match(sql_keywords, line):
                    in_sql = True
                if in_sql:
                    sql_lines.append(line)
                if line.endswith(";"):
                    in_sql = False

            if sql_lines:
                query = "\n".join(sql_lines)

        # 注意：移除首尾反引号。
        query = query.strip("`")

        # 注意：移除首尾引号。
        query = query.strip()
        if (query.startswith('"') and query.endswith('"')) or (query.startswith("'") and query.endswith("'")):
            query = query[1:-1]

        # 注意：清理多余空白并处理残余反引号。
        query = query.strip()
        # 注意：仅移除非标识符内的反引号。
        return re.sub(r"`(?![a-zA-Z0-9_])|(?<![a-zA-Z0-9_])`", "", query)

    def execute_sql(self) -> DataFrame:
        """执行 `SQL` 并返回结果 `DataFrame`。
        契约：成功返回查询结果；失败抛 `ValueError`。
        关键路径：读取凭证 → 构建客户端 → 清洗 `SQL` → 执行查询。
        决策：空查询直接报错。问题：避免空执行；方案：提前校验；代价：需要显式输入；重评：当允许空查询触发默认行为时。
        """
        try:
        # 注意：先读取服务账号文件并解析 `project_id`。
            try:
                service_account_path = Path(self.service_account_json_file)
                with service_account_path.open() as f:
                    credentials_json = json.load(f)
                    project_id = credentials_json.get("project_id")
                    if not project_id:
                        msg = "No project_id found in service account credentials file."
                        raise ValueError(msg)
            except FileNotFoundError as e:
                msg = f"Service account file not found: {e}"
                raise ValueError(msg) from e
            except json.JSONDecodeError as e:
                msg = "Invalid JSON string for service account credentials"
                raise ValueError(msg) from e

        # 注意：加载服务账号凭证。
            try:
                credentials = Credentials.from_service_account_file(self.service_account_json_file)
            except Exception as e:
                msg = f"Error loading service account credentials: {e}"
                raise ValueError(msg) from e

        except ValueError:
            raise
        except Exception as e:
            msg = f"Error executing BigQuery SQL query: {e}"
            raise ValueError(msg) from e

        try:
            client = bigquery.Client(credentials=credentials, project=project_id)

            # 注意：空查询直接失败，避免无效执行。
            if not str(self.query).strip():
                msg = "No valid SQL query found in input text."
                raise ValueError(msg)

            # 注意：包含代码块/引号或显式开启清洗时，执行清洗。
            if "```" in str(self.query) or '"' in str(self.query) or "'" in str(self.query) or self.clean_query:
                sql_query = self._clean_sql_query(str(self.query))
            else:
                sql_query = str(self.query).strip()  # 注意：至少去除首尾空白

            query_job = client.query(sql_query)
            results = query_job.result()
            output_dict = [dict(row) for row in results]

        except RefreshError as e:
            msg = "Authentication error: Unable to refresh authentication token. Please try to reauthenticate."
            raise ValueError(msg) from e
        except Exception as e:
            msg = f"Error executing BigQuery SQL query: {e}"
            raise ValueError(msg) from e

        return DataFrame(output_dict)
