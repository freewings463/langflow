"""
模块名称：apify_actor

本模块提供 Apify Actor 组件，实现运行 Actor、拉取数据集并转为 `Data`。
主要功能包括：
- 功能1：封装 Apify Actor 运行与数据集读取流程。
- 功能2：将 Actor 输入 schema 转换为工具可用的输入模型。
- 功能3：提供字段筛选与结构扁平化能力。

使用场景：在 Langflow 流程中调用 Apify Actor 进行数据采集或作为工具使用。
关键组件：
- 类 `ApifyActorsComponent`

设计背景：将 Apify Actor 调用细节统一封装，减少流程中重复配置。
注意事项：需要有效的 Apify Token；数据集字段筛选会替换嵌套字段分隔符。
"""

import json
import string
from typing import Any, cast

from apify_client import ApifyClient
from langchain_community.document_loaders.apify_dataset import ApifyDatasetLoader
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_serializer

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Tool
from lfx.inputs.inputs import BoolInput
from lfx.io import MultilineInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

MAX_DESCRIPTION_LEN = 250  # 注意：Actor 字段描述的截断上限，避免工具描述过长。


class ApifyActorsComponent(Component):
    """Apify Actor 组件，负责运行 Actor 并输出结构化数据。

    契约：输入包含 `apify_token`/`actor_id`/`run_input`；输出为 `list[Data]` 或工具。
    关键路径：
    1) `run_model` 解析输入并调用 `run_actor`；
    2) `run_actor` 拉取日志、等待完成并读取数据集；
    3) 输出结果可按字段筛选与扁平化。
    异常流：Token 缺失、Actor/Build/Dataset 不存在会抛 `ValueError`。
    排障入口：运行日志通过 `self.log` 输出；HTTP 异常由 Apify SDK 处理。
    决策：
    问题：Apify Actor 运行涉及多步 API 调用，流程容易分散在节点逻辑中。
    方案：集中封装到组件，并提供工具化入口。
    代价：依赖 Apify SDK 与 LangChain loader，版本变动需同步调整。
    重评：当 Apify 提供统一批处理 API 或组件框架变化时。
    """
    display_name = "Apify Actors"
    description = (
        "Use Apify Actors to extract data from hundreds of places fast. "
        "This component can be used in a flow to retrieve data or as a tool with an agent."
    )
    documentation: str = "https://docs.langflow.org/bundles-apify"
    icon = "Apify"
    name = "ApifyActors"

    inputs = [
        SecretStrInput(
            name="apify_token",
            display_name="Apify Token",
            info="The API token for the Apify account.",
            required=True,
            password=True,
        ),
        StrInput(
            name="actor_id",
            display_name="Actor",
            info=(
                "Actor name from Apify store to run. For example 'apify/website-content-crawler' "
                "to use the Website Content Crawler Actor."
            ),
            value="apify/website-content-crawler",
            required=True,
        ),
        # 注意：多行输入更适合复杂 JSON，避免嵌套字典输入不易编辑。
        MultilineInput(
            name="run_input",
            display_name="Run input",
            info=(
                'The JSON input for the Actor run. For example for the "apify/website-content-crawler" Actor: '
                '{"startUrls":[{"url":"https://docs.apify.com/academy/web-scraping-for-beginners"}],"maxCrawlDepth":0}'
            ),
            value='{"startUrls":[{"url":"https://docs.apify.com/academy/web-scraping-for-beginners"}],"maxCrawlDepth":0}',
            required=True,
        ),
        MultilineInput(
            name="dataset_fields",
            display_name="Output fields",
            info=(
                "Fields to extract from the dataset, split by commas. "
                "Other fields will be ignored. Dots in nested structures will be replaced by underscores. "
                "Sample input: 'text, metadata.title'. "
                "Sample output: {'text': 'page content here', 'metadata_title': 'page title here'}. "
                "For example, for the 'apify/website-content-crawler' Actor, you can extract the 'markdown' field, "
                "which is the content of the website in markdown format."
            ),
        ),
        BoolInput(
            name="flatten_dataset",
            display_name="Flatten output",
            info=(
                "The output dataset will be converted from a nested format to a flat structure. "
                "Dots in nested structure will be replaced by underscores. "
                "This is useful for further processing of the Data object. "
                "For example, {'a': {'b': 1}} will be flattened to {'a_b': 1}."
            ),
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", type_=list[Data], method="run_model"),
        Output(display_name="Tool", name="tool", type_=Tool, method="build_tool"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        """初始化组件并准备 Apify 客户端缓存。

        契约：初始化后 `_apify_client` 为空，首次调用时延迟创建。
        关键路径：调用父类初始化 -> 设置 `_apify_client`。
        决策：
        问题：Apify 客户端创建依赖 token 且可能随 token 变化。
        方案：延迟创建并缓存在实例中。
        代价：首次调用时会有创建成本。
        重评：当需要多 token 并行或客户端池化时。
        """
        super().__init__(*args, **kwargs)
        self._apify_client: ApifyClient | None = None

    def run_model(self) -> list[Data]:
        """运行 Actor 并返回 `Data` 列表。

        契约：`run_input` 为 JSON 字符串；输出为 `list[Data]`。
        关键路径：解析输入 -> 运行 Actor -> 可选扁平化 -> 包装为 `Data`。
        异常流：JSON 解析失败或 Actor 运行失败会抛异常。
        决策：
        问题：输出需要统一为 `Data` 以便下游处理。
        方案：将数据集项逐条包装为 `Data(data=item)`。
        代价：大数据集会产生较多对象。
        重评：当需要流式输出或分页加载时。
        """
        input_ = json.loads(self.run_input)
        fields = ApifyActorsComponent.parse_dataset_fields(self.dataset_fields) if self.dataset_fields else None
        res = self.run_actor(self.actor_id, input_, fields=fields)
        if self.flatten_dataset:
            res = [ApifyActorsComponent.flatten(item) for item in res]
        data = [Data(data=item) for item in res]

        self.status = data
        return data

    def build_tool(self) -> Tool:
        """构建可供 Agent 调用的 Apify Actor 工具。

        契约：返回 `Tool` 实例；工具输入为 JSON 字符串。
        关键路径：读取 Actor build -> 解析输入 schema -> 生成输入模型 -> 生成工具类。
        异常流：Actor build 或输入 schema 缺失会抛 `ValueError`。
        决策：
        问题：Agent 需要基于 Actor schema 自动生成可用输入提示。
        方案：从 build 中提取 schema 并构建 Pydantic 输入模型。
        代价：schema 描述较长时需要截断处理。
        重评：当 Apify 提供更稳定的 schema API 或工具标准变化时。
        """
        actor_id = self.actor_id

        build = self._get_actor_latest_build(actor_id)
        readme = build.get("readme", "")[:250] + "..."
        if not (input_schema_str := build.get("inputSchema")):
            msg = "Input schema not found"
            raise ValueError(msg)
        input_schema = json.loads(input_schema_str)
        properties, required = ApifyActorsComponent.get_actor_input_schema_from_build(input_schema)
        properties = {"run_input": properties}

        # 注意：工具输入严格遵循 Actor 输入 schema。
        info_ = [
            (
                "JSON encoded as a string with input schema (STRICTLY FOLLOW JSON FORMAT AND SCHEMA):\n\n"
                f"{json.dumps(properties, separators=(',', ':'))}"
            )
        ]
        if required:
            info_.append("\n\nRequired fields:\n" + "\n".join(required))

        info = "".join(info_)

        input_model_cls = ApifyActorsComponent.create_input_model_class(info)
        tool_cls = ApifyActorsComponent.create_tool_class(self, readme, input_model_cls, actor_id)

        return cast("Tool", tool_cls())

    @staticmethod
    def create_tool_class(
        parent: "ApifyActorsComponent", readme: str, input_model: type[BaseModel], actor_id: str
    ) -> type[BaseTool]:
        """创建运行 Apify Actor 的工具类。

        契约：返回 `BaseTool` 子类，运行时调用 `parent.run_actor`。
        关键路径：构建工具元信息 -> 绑定输入 schema -> 定义 `_run`。
        异常流：工具运行阶段异常由 `run_actor` 抛出。
        决策：
        问题：工具类需要携带 Actor README 片段用于提示。
        方案：将 README 片段嵌入工具描述。
        代价：描述长度受限，可能丢失部分上下文。
        重评：当工具系统支持外部文档链接时。
        """

        class ApifyActorRun(BaseTool):
            """运行 Apify Actor 的工具实现。

            契约：`_run` 接收 JSON 字符串或字典，返回拼接后的 JSON 文本。
            关键路径：解析输入 -> 调用 Actor -> 序列化输出。
            异常流：JSON 解析失败或 Actor 失败会抛异常。
            决策：
            问题：Agent 输出需要可读且易于二次解析。
            方案：将每条结果序列化为 JSON 并用空行分隔。
            代价：大结果会产生较长文本。
            重评：当工具支持结构化返回类型时。
            """

            name: str = f"apify_actor_{ApifyActorsComponent.actor_id_to_tool_name(actor_id)}"
            description: str = (
                "Run an Apify Actor with the given input. "
                "Here is a part of the currently loaded Actor README:\n\n"
                f"{readme}\n\n"
            )

            args_schema: type[BaseModel] = input_model

            @field_serializer("args_schema")
            def serialize_args_schema(self, args_schema):
                return args_schema.schema()

            def _run(self, run_input: str | dict) -> str:
                """执行 Actor 并返回结果文本。

                契约：`run_input` 为 JSON 字符串或字典；返回多条 JSON 文本拼接。
                关键路径：解析输入 -> 运行 Actor -> 序列化结果。
                异常流：JSON 解析失败或 Actor 失败会抛异常。
                决策：
                问题：工具输出需易读且可被二次解析。
                方案：逐条转为 JSON 字符串并以空行分隔。
                代价：输出文本较长且非结构化对象。
                重评：当工具支持返回结构化列表时。
                """
                input_dict = json.loads(run_input) if isinstance(run_input, str) else run_input

                # 注意：兼容嵌套输入结构（如 `{"run_input": {...}}`）。
                input_dict = input_dict.get("run_input", input_dict)

                res = parent.run_actor(actor_id, input_dict)
                return "\n\n".join([ApifyActorsComponent.dict_to_json_str(item) for item in res])

        return ApifyActorRun

    @staticmethod
    def create_input_model_class(description: str) -> type[BaseModel]:
        """创建 Actor 输入的 Pydantic 模型类。

        契约：返回包含 `run_input` 字段的 `BaseModel` 子类。
        关键路径：定义字段 -> 绑定描述 -> 返回模型类。
        决策：
        问题：工具输入需要具备可读的 schema 描述。
        方案：使用 `Field` 将 schema 文本注入描述。
        代价：描述过长时会影响提示长度。
        重评：当工具系统改用 JSON Schema 直接输入时。
        """

        class ActorInput(BaseModel):
            """Apify Actor 工具输入模型。"""

            run_input: str = Field(..., description=description)

        return ActorInput

    def _get_apify_client(self) -> ApifyClient:
        """获取或创建 Apify 客户端。

        契约：`apify_token` 必须存在；token 变化时重建客户端。
        关键路径：校验 token -> 判断缓存 -> 创建客户端并追加 UA。
        异常流：token 缺失抛 `ValueError`。
        决策：
        问题：token 可能在运行期间更新，需避免复用旧客户端。
        方案：比较 token 并在变化时重建。
        代价：重建会产生新的 HTTP 连接池。
        重评：当组件支持多 token 并发时。
        """
        if not self.apify_token:
            msg = "API token is required."
            raise ValueError(msg)
        # 注意：token 变更时重建客户端，避免使用旧凭据。
        if self._apify_client is None or self._apify_client.token != self.apify_token:
            self._apify_client = ApifyClient(self.apify_token)
            if httpx_client := self._apify_client.http_client.httpx_client:
                httpx_client.headers["user-agent"] += "; Origin/langflow"
        return self._apify_client

    def _get_actor_latest_build(self, actor_id: str) -> dict:
        """获取 Actor 的默认构建版本信息。

        契约：`actor_id` 必须存在于 Apify；返回 build 字典。
        关键路径：读取 Actor 信息 -> 获取默认 build tag -> 拉取 build 详情。
        异常流：Actor/Build 不存在时抛 `ValueError`。
        决策：
        问题：Actor 可能有多个 build，需要确定默认版本。
        方案：使用 `defaultRunOptions.build` 对应的 buildId。
        代价：依赖 Actor 配置的默认值，可能不是最新构建。
        重评：当需要显式指定 build 版本时。
        """
        client = self._get_apify_client()
        actor = client.actor(actor_id=actor_id)
        if not (actor_info := actor.get()):
            msg = f"Actor {actor_id} not found."
            raise ValueError(msg)

        default_build_tag = actor_info.get("defaultRunOptions", {}).get("build")
        latest_build_id = actor_info.get("taggedBuilds", {}).get(default_build_tag, {}).get("buildId")

        if (build := client.build(latest_build_id).get()) is None:
            msg = f"Build {latest_build_id} not found."
            raise ValueError(msg)

        return build

    @staticmethod
    def get_actor_input_schema_from_build(input_schema: dict) -> tuple[dict, list[str]]:
        """从 Actor build 的 schema 中提取输入字段与必填项。

        契约：返回 `(properties, required)`；描述字段会截断到 `MAX_DESCRIPTION_LEN`。
        关键路径：读取 `properties/required` -> 截断描述 -> 提取常用字段。
        决策：
        问题：完整 schema 描述过长，不利于工具提示。
        方案：对 `description` 做截断，保留核心字段。
        代价：长描述信息丢失。
        重评：当工具支持折叠或外链展示时。
        """
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        properties_out: dict = {}
        for item, meta in properties.items():
            properties_out[item] = {}
            if desc := meta.get("description"):
                properties_out[item]["description"] = (
                    desc[:MAX_DESCRIPTION_LEN] + "..." if len(desc) > MAX_DESCRIPTION_LEN else desc
                )
            for key_name in ("type", "default", "prefill", "enum"):
                if value := meta.get(key_name):
                    properties_out[item][key_name] = value

        return properties_out, required

    def _get_run_dataset_id(self, run_id: str) -> str:
        """根据 run_id 获取数据集 ID。

        契约：run_id 必须存在并关联数据集；返回 dataset id。
        关键路径：获取 run -> 读取 dataset -> 提取 `id`。
        异常流：数据集或 id 缺失时抛 `ValueError`。
        决策：
        问题：Actor 运行结果通过数据集读取，需要唯一 ID。
        方案：通过 run 客户端查询 dataset 元信息。
        代价：增加一次 API 调用。
        重评：当 Actor 直接返回数据集 ID 时。
        """
        client = self._get_apify_client()
        run = client.run(run_id=run_id)
        if (dataset := run.dataset().get()) is None:
            msg = "Dataset not found"
            raise ValueError(msg)
        if (did := dataset.get("id")) is None:
            msg = "Dataset id not found"
            raise ValueError(msg)
        return did

    @staticmethod
    def dict_to_json_str(d: dict) -> str:
        """将字典安全序列化为 JSON 字符串。

        契约：无法序列化的值以 `"<n/a>"` 替代。
        关键路径：`json.dumps` + `default` 回退。
        决策：
        问题：Actor 输出可能包含不可序列化对象。
        方案：使用 `default` 回退占位。
        代价：丢失不可序列化对象的真实内容。
        重评：当需要结构化保留非 JSON 对象时。
        """
        return json.dumps(d, separators=(",", ":"), default=lambda _: "<n/a>")

    @staticmethod
    def actor_id_to_tool_name(actor_id: str) -> str:
        """将 actor_id 转换为合法工具名。

        契约：仅保留字母/数字/下划线/短横线，其余字符替换为 `_`。
        关键路径：遍历字符并替换非法字符。
        决策：
        问题：工具名需符合 LangChain 工具命名约束。
        方案：对非法字符统一替换为 `_`。
        代价：不同 actor_id 可能映射为相同名称。
        重评：当工具系统支持更宽松命名时。
        """
        valid_chars = string.ascii_letters + string.digits + "_-"
        return "".join(char if char in valid_chars else "_" for char in actor_id)

    def run_actor(self, actor_id: str, run_input: dict, fields: list[str] | None = None) -> list[dict]:
        """运行 Apify Actor 并返回数据集内容。

        契约：`run_input` 为 Actor 期望的 JSON；`fields` 可选筛选字段。
        关键路径：
        1) 调用 Actor 并获取 run_id；
        2) 订阅日志并等待完成；
        3) 拉取数据集并可选字段映射。
        异常流：run_id/客户端缺失会抛 `ValueError`。
        排障入口：运行日志会通过 `self.log` 输出。
        决策：
        问题：Actor 输出位于数据集，需先等待完成再读取。
        方案：阻塞等待 `wait_for_finish` 后读取数据集。
        代价：同步等待可能占用执行线程。
        重评：当引入异步执行或轮询调度时。
        """
        client = self._get_apify_client()
        if (details := client.actor(actor_id=actor_id).call(run_input=run_input, wait_secs=1)) is None:
            msg = "Actor run details not found"
            raise ValueError(msg)
        if (run_id := details.get("id")) is None:
            msg = "Run id not found"
            raise ValueError(msg)

        if (run_client := client.run(run_id)) is None:
            msg = "Run client not found"
            raise ValueError(msg)

        # 排障：实时拉取 Actor 日志，便于定位运行问题。
        with run_client.log().stream() as response:
            if response:
                for line in response.iter_lines():
                    self.log(line)
        run_client.wait_for_finish()

        dataset_id = self._get_run_dataset_id(run_id)

        loader = ApifyDatasetLoader(
            dataset_id=dataset_id,
            dataset_mapping_function=lambda item: item
            if not fields
            else {k.replace(".", "_"): ApifyActorsComponent.get_nested_value(item, k) for k in fields},
        )
        return loader.load()

    @staticmethod
    def get_nested_value(data: dict[str, Any], key: str) -> Any:
        """按点号路径读取嵌套字典值。

        契约：路径不存在时返回 `None`。
        关键路径：逐层检查字典并下钻。
        决策：
        问题：数据集字段筛选可能包含嵌套路径。
        方案：按 `a.b.c` 形式逐级取值。
        代价：仅支持字典结构，不支持列表索引。
        重评：当需要支持数组路径时。
        """
        keys = key.split(".")
        value = data
        for k in keys:
            if not isinstance(value, dict) or k not in value:
                return None
            value = value[k]
        return value

    @staticmethod
    def parse_dataset_fields(dataset_fields: str) -> list[str]:
        """将逗号分隔字段字符串转换为字段列表。

        契约：自动去除引号与空白。
        关键路径：清理引号 -> 分割 -> 去空格。
        决策：
        问题：用户输入包含引号与空格，解析易出错。
        方案：统一清理引号并 `strip()`。
        代价：无法保留字段名中的引号。
        重评：当提供结构化字段输入控件时。
        """
        dataset_fields = dataset_fields.replace("'", "").replace('"', "").replace("`", "")
        return [field.strip() for field in dataset_fields.split(",")]

    @staticmethod
    def flatten(d: dict) -> dict:
        """将嵌套字典扁平化为单层字典。

        契约：嵌套键使用 `_` 连接；非字典值原样保留。
        关键路径：递归展开 -> 拼接键名 -> 汇总结果。
        决策：
        问题：下游处理更倾向于扁平结构。
        方案：递归展开嵌套字典并拼接键名。
        代价：可能与原有 `_` 命名冲突。
        重评：当下游支持嵌套结构时。
        """

        def items():
            for key, value in d.items():
                if isinstance(value, dict):
                    for subkey, subvalue in ApifyActorsComponent.flatten(value).items():
                        yield key + "_" + subkey, subvalue
                else:
                    yield key, value

        return dict(items())
