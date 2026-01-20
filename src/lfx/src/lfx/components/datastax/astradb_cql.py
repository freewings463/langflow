"""
模块名称：AstraDB CQL 工具组件

本模块提供基于 Astra DB CQL 表的查询工具封装，将 REST API 查询结果转换为 `Data`。主要功能包括：
- 根据工具参数生成过滤条件并调用 Astra REST API
- 构建可供 LLM 调用的结构化工具

关键组件：
- `AstraDBCQLToolComponent`

设计背景：需要在 LFX 中以工具形式访问 CQL 表数据。
使用场景：LLM 调用查询事务数据或结构化记录。
注意事项：依赖网络请求与 REST API，错误会抛 `ValueError`。
"""

import json
import urllib
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

import requests
from langchain_core.tools import StructuredTool, Tool
from pydantic import BaseModel, Field, create_model

from lfx.base.datastax.astradb_base import AstraDBBaseComponent
from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.io import DictInput, IntInput, StrInput, TableInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.table import EditMode


class AstraDBCQLToolComponent(AstraDBBaseComponent, LCToolComponent):
    """AstraDB CQL 工具组件

    契约：输入工具定义与过滤参数；输出 `Tool` 或查询结果 `Data`；
    副作用：发起 REST 请求并更新 `self.status`；
    失败语义：HTTP 错误或解析失败抛 `ValueError`。
    关键路径：1) 构建工具参数模型 2) 生成 REST 查询 3) 转换结果为 `Data`。
    决策：通过 REST API 而非 CQL 驱动执行查询。
    问题：避免在组件中引入重量级驱动依赖。
    方案：调用 Astra REST API 并拼装 `where` 条件。
    代价：功能受 REST API 限制，复杂查询不支持。
    重评：当需要复杂查询或更高性能时。
    """
    display_name: str = "Astra DB CQL"
    description: str = "Create a tool to get transactional data from DataStax Astra DB CQL Table"
    documentation: str = "https://docs.langflow.org/bundles-datastax"
    icon: str = "AstraDB"

    inputs = [
        *AstraDBBaseComponent.inputs,
        StrInput(name="tool_name", display_name="Tool Name", info="The name of the tool.", required=True),
        StrInput(
            name="tool_description",
            display_name="Tool Description",
            info="The tool description to be passed to the model.",
            required=True,
        ),
        StrInput(
            name="projection_fields",
            display_name="Projection fields",
            info="Attributes to return separated by comma.",
            required=True,
            value="*",
            advanced=True,
        ),
        TableInput(
            name="tools_params",
            display_name="Tools Parameters",
            info="Define the structure for the tool parameters. Describe the parameters "
            "in a way the LLM can understand how to use them. Add the parameters "
            "respecting the table schema (Partition Keys, Clustering Keys and Indexed Fields).",
            required=False,
            table_schema=[
                {
                    "name": "name",
                    "display_name": "Name",
                    "type": "str",
                    "description": "Name of the field/parameter to be used by the model.",
                    "default": "field",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "field_name",
                    "display_name": "Field Name",
                    "type": "str",
                    "description": "Specify the column name to be filtered on the table. "
                    "Leave empty if the attribute name is the same as the name of the field.",
                    "default": "",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "description",
                    "display_name": "Description",
                    "type": "str",
                    "description": "Describe the purpose of the parameter.",
                    "default": "description of tool parameter",
                    "edit_mode": EditMode.POPOVER,
                },
                {
                    "name": "mandatory",
                    "display_name": "Is Mandatory",
                    "type": "boolean",
                    "edit_mode": EditMode.INLINE,
                    "description": ("Indicate if the field is mandatory."),
                    "options": ["True", "False"],
                    "default": "False",
                },
                {
                    "name": "is_timestamp",
                    "display_name": "Is Timestamp",
                    "type": "boolean",
                    "edit_mode": EditMode.INLINE,
                    "description": ("Indicate if the field is a timestamp."),
                    "options": ["True", "False"],
                    "default": "False",
                },
                {
                    "name": "operator",
                    "display_name": "Operator",
                    "type": "str",
                    "description": "Set the operator for the field. "
                    "https://docs.datastax.com/en/astra-db-serverless/api-reference/documents.html#operators",
                    "default": "$eq",
                    "options": ["$gt", "$gte", "$lt", "$lte", "$eq", "$ne", "$in", "$nin", "$exists", "$all", "$size"],
                    "edit_mode": EditMode.INLINE,
                },
            ],
            value=[],
        ),
        DictInput(
            name="partition_keys",
            display_name="DEPRECATED: Partition Keys",
            is_list=True,
            info="Field name and description to the model",
            required=False,
            advanced=True,
        ),
        DictInput(
            name="clustering_keys",
            display_name="DEPRECATED: Clustering Keys",
            is_list=True,
            info="Field name and description to the model",
            required=False,
            advanced=True,
        ),
        DictInput(
            name="static_filters",
            display_name="Static Filters",
            is_list=True,
            advanced=True,
            info="Field name and value. When filled, it will not be generated by the LLM.",
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=5,
        ),
    ]

    def parse_timestamp(self, timestamp_str: str) -> str:
        """解析时间字符串为 Astra REST API 可用格式

        契约：输入时间字符串，输出 `YYYY-MM-DDTHH:MI:SS.000Z`；副作用：无；
        失败语义：无法解析时抛 `ValueError`。
        关键路径：逐个格式尝试 -> 统一转 UTC -> 格式化输出。
        决策：支持多种常见日期格式而非仅 ISO8601。
        问题：上游可能提供不同格式时间。
        方案：遍历常见格式并统一到 UTC。
        代价：格式覆盖不全时仍会失败。
        重评：当需要更强的日期解析库时。
        """
        formats = [
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y",
            "%Y/%m/%d",
        ]

        for fmt in formats:
            try:
                date_obj = datetime.strptime(timestamp_str, fmt).astimezone()

                if date_obj.tzinfo is None:
                    date_obj = date_obj.replace(tzinfo=timezone.utc)

                utc_date = date_obj.astimezone(timezone.utc)
                return utc_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except ValueError:
                continue

        msg = f"Could not parse date: {timestamp_str}"
        logger.error(msg)
        raise ValueError(msg)

    def astra_rest(self, args):
        """执行 Astra REST API 查询

        契约：输入工具参数字典，输出 REST 返回的 `data` 列表或状态码；
        副作用：发起网络请求；
        失败语义：HTTP >= 400 抛 `ValueError`，JSON 解析失败返回状态码。
        关键路径：1) 构建 `where` 条件 2) 拼接 URL 3) 发送请求并解析响应。
        决策：将 timestamp 字段在请求前转换为 API 格式。
        问题：REST API 对日期字段要求固定格式。
        方案：统一在请求前调用 `parse_timestamp`。
        代价：格式不匹配将导致请求失败。
        重评：当 API 支持更多日期格式或服务端解析时。
        """
        headers = {"Accept": "application/json", "X-Cassandra-Token": f"{self.token}"}
        astra_url = f"{self.get_api_endpoint()}/api/rest/v2/keyspaces/{self.get_keyspace()}/{self.collection_name}/"
        where = {}

        for param in self.tools_params:
            field_name = param["field_name"] if param["field_name"] else param["name"]
            field_value = None

            if field_name in self.static_filters:
                field_value = self.static_filters[field_name]
            elif param["name"] in args:
                field_value = args[param["name"]]

            if field_value is None:
                continue

            if param["is_timestamp"] == True:  # noqa: E712
                try:
                    field_value = self.parse_timestamp(field_value)
                except ValueError as e:
                    msg = f"Error parsing timestamp: {e} - Use the prompt to specify the date in the correct format"
                    logger.error(msg)
                    raise ValueError(msg) from e

            if param["operator"] == "$exists":
                where[field_name] = {**where.get(field_name, {}), param["operator"]: True}
            elif param["operator"] in ["$in", "$nin", "$all"]:
                where[field_name] = {
                    **where.get(field_name, {}),
                    param["operator"]: field_value.split(",") if isinstance(field_value, str) else field_value,
                }
            else:
                where[field_name] = {**where.get(field_name, {}), param["operator"]: field_value}

        url = f"{astra_url}?page-size={self.number_of_results}"
        url += f"&where={json.dumps(where)}"

        if self.projection_fields != "*":
            url += f"&fields={urllib.parse.quote(self.projection_fields.replace(' ', ''))}"

        res = requests.request("GET", url=url, headers=headers, timeout=10)

        if int(res.status_code) >= HTTPStatus.BAD_REQUEST:
            msg = f"Error on Astra DB CQL Tool {self.tool_name} request: {res.text}"
            logger.error(msg)
            raise ValueError(msg)

        try:
            res_data = res.json()
            return res_data["data"]
        except ValueError:
            return res.status_code

    def create_args_schema(self) -> dict[str, BaseModel]:
        """构建工具入参的 Pydantic Schema

        契约：基于 `tools_params` 生成 `ToolInput` 模型；副作用：无；
        失败语义：参数缺失导致的 KeyError 由调用方处理。
        关键路径：1) 过滤静态参数 2) 生成字段定义 3) 创建动态模型。
        决策：静态过滤条件不暴露给 LLM。
        问题：部分参数应固定以避免被模型覆盖。
        方案：从 `static_filters` 里剔除字段。
        代价：降低工具灵活性。
        重评：当需要动态调整静态过滤条件时。
        """
        args: dict[str, tuple[Any, Field]] = {}

        for param in self.tools_params:
            field_name = param["field_name"] if param["field_name"] else param["name"]
            if field_name not in self.static_filters:
                if param["mandatory"]:
                    args[param["name"]] = (str, Field(description=param["description"]))
                else:
                    args[param["name"]] = (str | None, Field(description=param["description"], default=None))

        model = create_model("ToolInput", **args, __base__=BaseModel)
        return {"ToolInput": model}

    def build_tool(self) -> Tool:
        """构建 AstraDB CQL 查询工具

        契约：返回 `StructuredTool`；副作用：无；
        失败语义：参数模型构建异常透传。
        关键路径：1) 生成入参 schema 2) 绑定 `run_model`。
        决策：使用结构化工具暴露给 LLM。
        问题：需要为模型提供清晰参数契约。
        方案：基于 `tools_params` 生成 Pydantic 模型。
        代价：参数变更需同步更新配置。
        重评：当采用统一的工具注册机制时。
        """
        schema_dict = self.create_args_schema()
        return StructuredTool.from_function(
            name=self.tool_name,
            args_schema=schema_dict["ToolInput"],
            description=self.tool_description,
            func=self.run_model,
            return_direct=False,
        )

    def projection_args(self, input_str: str) -> dict:
        """解析投影字段定义

        契约：输入逗号分隔字段字符串，输出投影字典；
        副作用：无；失败语义：无。
        关键路径：拆分字段并生成 include/exclude 映射。
        决策：以 `!` 前缀表示排除字段。
        问题：需要在工具层控制返回字段。
        方案：将字符串解析为 Astra REST API 需要的投影格式。
        代价：输入格式错误可能导致投影异常。
        重评：当改用结构化字段输入时。
        """
        elements = input_str.split(",")
        result = {}

        for element in elements:
            if element.startswith("!"):
                result[element[1:]] = False
            else:
                result[element] = True

        return result

    def run_model(self, **args) -> Data | list[Data]:
        """执行查询并转换为 `Data`

        契约：输入工具参数，输出 `list[Data]`；副作用：更新 `self.status`；
        失败语义：查询失败抛异常或返回空结果。
        关键路径：1) 调用 `astra_rest` 2) 转换结果为 `Data` 3) 写入状态。
        决策：非列表结果视为异常并返回空列表。
        问题：REST API 可能返回非 `data` 列表结构。
        方案：仅在结果为列表时转换。
        代价：无法返回错误响应体。
        重评：当需要统一错误结构或更丰富的错误输出时。
        """
        results = self.astra_rest(args)
        data: list[Data] = []

        if isinstance(results, list):
            data = [Data(data=doc) for doc in results]
        else:
            self.status = results
            return []

        self.status = data
        return data
