"""
模块名称：AstraDB 集合查询工具组件

本模块提供基于 Astra DB Collection 的查询工具封装，支持向量/语义/元数据过滤。主要功能包括：
- 生成结构化工具参数与过滤条件
- 根据配置执行向量搜索或元数据搜索

关键组件：
- `AstraDBToolComponent`

设计背景：需要以工具形式让 LLM 访问 Astra DB 集合数据。
使用场景：LLM 调用混合检索或元数据检索。
注意事项：依赖 `astrapy` 与外部网络，错误会抛 `ValueError`。
"""

from datetime import datetime, timezone
from typing import Any

from astrapy import Collection, DataAPIClient, Database
from langchain_core.tools import StructuredTool, Tool
from pydantic import BaseModel, Field, create_model

from lfx.base.datastax.astradb_base import AstraDBBaseComponent
from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.io import BoolInput, DictInput, IntInput, StrInput, TableInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.table import EditMode


class AstraDBToolComponent(AstraDBBaseComponent, LCToolComponent):
    """AstraDB 集合查询工具组件

    契约：输入工具定义、过滤参数与检索配置；输出 `Tool` 或 `Data` 列表；
    副作用：调用 Astra DB API 并更新 `self.status`；
    失败语义：依赖缺失或查询失败抛 `ValueError`。
    关键路径：1) 构建工具参数模型 2) 生成过滤/向量查询 3) 执行查询并转换结果。
    决策：保留 v1/v2 参数结构以兼容旧配置。
    问题：历史配置已被使用，直接迁移成本高。
    方案：同时支持 `tool_params` 与 `tools_params_v2`。
    代价：维护两套逻辑增加复杂度。
    重评：当旧配置弃用且无存量用户时。
    """
    display_name: str = "Astra DB Tool"
    description: str = "Tool to run hybrid vector and metadata search on DataStax Astra DB Collection"
    documentation: str = "https://docs.langflow.org/bundles-datastax"
    icon: str = "AstraDB"
    legacy: bool = True
    name = "AstraDBTool"
    replacement = ["datastax.AstraDB"]

    inputs = [
        *AstraDBBaseComponent.inputs,
        StrInput(
            name="tool_name",
            display_name="Tool Name",
            info="The name of the tool to be passed to the LLM.",
            required=True,
        ),
        StrInput(
            name="tool_description",
            display_name="Tool Description",
            info="Describe the tool to LLM. Add any information that can help the LLM to use the tool.",
            required=True,
        ),
        StrInput(
            name="projection_attributes",
            display_name="Projection Attributes",
            info="Attributes to be returned by the tool separated by comma.",
            required=True,
            value="*",
            advanced=True,
        ),
        TableInput(
            name="tools_params_v2",
            display_name="Tools Parameters",
            info="Define the structure for the tool parameters. Describe the parameters "
            "in a way the LLM can understand how to use them.",
            required=False,
            table_schema=[
                {
                    "name": "name",
                    "display_name": "Name",
                    "type": "str",
                    "description": "Specify the name of the output field/parameter for the model.",
                    "default": "field",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "attribute_name",
                    "display_name": "Attribute Name",
                    "type": "str",
                    "description": "Specify the attribute name to be filtered on the collection. "
                    "Leave empty if the attribute name is the same as the name of the field.",
                    "default": "",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "description",
                    "display_name": "Description",
                    "type": "str",
                    "description": "Describe the purpose of the output field.",
                    "default": "description of field",
                    "edit_mode": EditMode.POPOVER,
                },
                {
                    "name": "metadata",
                    "display_name": "Is Metadata",
                    "type": "boolean",
                    "edit_mode": EditMode.INLINE,
                    "description": ("Indicate if the field is included in the metadata field."),
                    "options": ["True", "False"],
                    "default": "False",
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
            name="tool_params",
            info="DEPRECATED: Attributes to filter and description to the model. "
            "Add ! for mandatory (e.g: !customerId)",
            display_name="Tool params",
            is_list=True,
            advanced=True,
        ),
        DictInput(
            name="static_filters",
            info="Attributes to filter and correspoding value",
            display_name="Static filters",
            advanced=True,
            is_list=True,
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=5,
        ),
        BoolInput(
            name="use_search_query",
            display_name="Semantic Search",
            info="When this parameter is activated, the search query parameter will be used to search the collection.",
            advanced=False,
            value=False,
        ),
        BoolInput(
            name="use_vectorize",
            display_name="Use Astra DB Vectorize",
            info="When this parameter is activated, Astra DB Vectorize method will be used to generate the embeddings.",
            advanced=False,
            value=False,
        ),
        StrInput(
            name="semantic_search_instruction",
            display_name="Semantic Search Instruction",
            info="The instruction to use for the semantic search.",
            required=True,
            value="Search query to find relevant documents.",
            advanced=True,
        ),
    ]

    _cached_client: DataAPIClient | None = None
    _cached_db: Database | None = None
    _cached_collection: Collection | None = None

    def create_args_schema(self) -> dict[str, BaseModel]:
        """构建旧版工具入参 Schema（兼容用）

        契约：返回 `ToolInput` 模型；副作用：记录警告日志；
        失败语义：参数异常透传。
        决策：保留旧版以兼容历史配置。
        问题：已有流程依赖旧版参数定义。
        方案：保持旧接口但提示迁移。
        代价：逻辑重复。
        重评：当旧配置完全淘汰后移除。
        """
        logger.warning("This is the old way to define the tool parameters. Please use the new way.")
        args: dict[str, tuple[Any, Field] | list[str]] = {}

        for key in self.tool_params:
            if key.startswith("!"):
                args[key[1:]] = (str, Field(description=self.tool_params[key]))
            else:
                args[key] = (str | None, Field(description=self.tool_params[key], default=None))

        if self.use_search_query:
            args["search_query"] = (
                str | None,
                Field(description="Search query to find relevant documents.", default=None),
            )

        model = create_model("ToolInput", **args, __base__=BaseModel)
        return {"ToolInput": model}

    def create_args_schema_v2(self) -> dict[str, BaseModel]:
        """构建新版工具入参 Schema

        契约：基于 `tools_params_v2` 构建 `ToolInput`；副作用：无；
        失败语义：参数缺失导致的 KeyError 由调用方处理。
        关键路径：遍历参数 -> 生成字段 -> 构建模型。
        决策：将 `search_query` 作为可选语义检索输入。
        问题：语义检索需要单独的查询输入。
        方案：在 schema 中追加 `search_query`。
        代价：调用方需知晓该字段。
        重评：当语义检索配置改为固定输入时。
        """
        args: dict[str, tuple[Any, Field] | list[str]] = {}

        for tool_param in self.tools_params_v2:
            if tool_param["mandatory"]:
                args[tool_param["name"]] = (str, Field(description=tool_param["description"]))
            else:
                args[tool_param["name"]] = (str | None, Field(description=tool_param["description"], default=None))

        if self.use_search_query:
            args["search_query"] = (
                str,
                Field(description=self.semantic_search_instruction),
            )

        model = create_model("ToolInput", **args, __base__=BaseModel)
        return {"ToolInput": model}

    def build_tool(self) -> Tool:
        """构建 Astra DB 查询工具

        契约：返回 `StructuredTool`；副作用：设置 `self.status`；
        失败语义：schema 构建异常透传。
        关键路径：1) 选择参数版本 2) 绑定 `run_model`。
        决策：当旧参数存在时优先旧版。
        问题：需要兼容旧参数定义。
        方案：根据 `tool_params` 是否为空切换。
        代价：行为不一致导致维护成本上升。
        重评：当旧参数弃用后统一为 v2。
        """
        schema_dict = self.create_args_schema() if len(self.tool_params.keys()) > 0 else self.create_args_schema_v2()

        tool = StructuredTool.from_function(
            name=self.tool_name,
            args_schema=schema_dict["ToolInput"],
            description=self.tool_description,
            func=self.run_model,
            return_direct=False,
        )
        self.status = "Astra DB Tool created"

        return tool

    def projection_args(self, input_str: str) -> dict | None:
        """构建投影字段参数

        契约：输入字段字符串，输出投影字典或 `None`；
        副作用：无；失败语义：无。
        关键路径：拆分字段并生成 include/exclude 映射。
        决策：强制排除 `$vector` 字段。
        问题：向量字段对工具结果无用且体积大。
        方案：在投影中显式排除 `$vector`。
        代价：无法在结果中直接返回向量。
        重评：当需要向量输出或做进一步处理时。
        """
        elements = input_str.split(",")
        result = {}

        if elements == ["*"]:
            return None

        result["$vector"] = False

        for element in elements:
            if element.startswith("!"):
                result[element[1:]] = False
            else:
                result[element] = True

        return result

    def parse_timestamp(self, timestamp_str: str) -> datetime:
        """解析时间字符串为 UTC `datetime`

        契约：输入时间字符串，输出 UTC `datetime`；副作用：无；
        失败语义：无法解析时抛 `ValueError`。
        关键路径：遍历格式 -> 解析 -> 转为 UTC。
        决策：支持多种常见格式而非只支持 ISO8601。
        问题：上游输入格式不固定。
        方案：按格式列表尝试解析。
        代价：格式未覆盖时仍会失败。
        重评：当引入更强日期解析库时。
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

                return date_obj.astimezone(timezone.utc)

            except ValueError:
                continue

        msg = f"Could not parse date: {timestamp_str}"
        logger.error(msg)
        raise ValueError(msg)

    def build_filter(self, args: dict, filter_settings: list) -> dict:
        """根据参数构建过滤条件

        契约：输入参数字典与过滤配置，输出过滤条件字典；
        副作用：无；失败语义：时间字段解析失败抛 `ValueError`。
        关键路径：1) 合并静态过滤 2) 处理动态参数 3) 生成操作符条件。
        决策：`search_query` 不进入过滤条件。
        问题：`search_query` 仅用于向量检索而非元数据过滤。
        方案：在构建过滤时跳过该字段。
        代价：无法使用 `search_query` 作为元数据过滤。
        重评：当需要混合过滤策略时。
        """
        filters = {**self.static_filters}

        for key, value in args.items():
            if key == "search_query":
                continue

            filter_setting = next((x for x in filter_settings if x["name"] == key), None)
            if filter_setting and value is not None:
                field_name = filter_setting["attribute_name"] if filter_setting["attribute_name"] else key
                filter_key = field_name if not filter_setting["metadata"] else f"metadata.{field_name}"
                if filter_setting["operator"] == "$exists":
                    filters[filter_key] = {**filters.get(filter_key, {}), filter_setting["operator"]: True}
                elif filter_setting["operator"] in ["$in", "$nin", "$all"]:
                    filters[filter_key] = {
                        **filters.get(filter_key, {}),
                        filter_setting["operator"]: value.split(",") if isinstance(value, str) else value,
                    }
                elif filter_setting["is_timestamp"] == True:  # noqa: E712
                    try:
                        filters[filter_key] = {
                            **filters.get(filter_key, {}),
                            filter_setting["operator"]: self.parse_timestamp(value),
                        }
                    except ValueError as e:
                        msg = f"Error parsing timestamp: {e} - Use the prompt to specify the date in the correct format"
                        logger.error(msg)
                        raise ValueError(msg) from e
                else:
                    filters[filter_key] = {**filters.get(filter_key, {}), filter_setting["operator"]: value}
        return filters

    def run_model(self, **args) -> Data | list[Data]:
        """执行查询并返回 `Data` 列表

        契约：输入工具参数，输出 `list[Data]`；副作用：更新 `self.status`；
        失败语义：查询失败抛 `ValueError`。
        关键路径（三步）：1) 构建过滤与向量检索条件 2) 调用 `find` 3) 转换结果为 `Data`。
        决策：当启用 `use_vectorize` 时使用 `$vectorize`。
        问题：向量生成方式可能由服务端或本地模型提供。
        方案：根据配置选择 `$vectorize` 或本地 embedding。
        代价：本地 embedding 需要额外模型依赖。
        重评：当统一改为服务端向量化时。
        """
        sort = {}

        filters = self.build_filter(args, self.tools_params_v2)

        if self.use_search_query and args["search_query"] is not None and args["search_query"] != "":
            if self.use_vectorize:
                sort["$vectorize"] = args["search_query"]
            else:
                if self.embedding is None:
                    msg = "Embedding model is not set. Please set the embedding model or use Astra DB Vectorize."
                    logger.error(msg)
                    raise ValueError(msg)
                embedding_query = self.embedding.embed_query(args["search_query"])
                sort["$vector"] = embedding_query
            del args["search_query"]

        find_options = {
            "filter": filters,
            "limit": self.number_of_results,
            "sort": sort,
        }

        projection = self.projection_args(self.projection_attributes)
        if projection and len(projection) > 0:
            find_options["projection"] = projection

        try:
            database = self.get_database_object(api_endpoint=self.get_api_endpoint())
            collection = database.get_collection(
                name=self.collection_name,
                keyspace=self.get_keyspace(),
            )
            results = collection.find(**find_options)
        except Exception as e:
            msg = f"Error on Astra DB Tool {self.tool_name} request: {e}"
            logger.error(msg)
            raise ValueError(msg) from e

        logger.info(f"Tool {self.tool_name} executed`")

        data: list[Data] = [Data(data=doc) for doc in results]
        self.status = data
        return data
