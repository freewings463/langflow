"""
模块名称：astra_assistants.util

本模块提供 Astra Assistants 与 Langflow 工具适配的公共辅助函数，主要用于
封装 OpenAI 客户端补丁、工具类扫描，以及 LangChain BaseTool 的动态包装。
主要功能包括：
- 拉取 LiteLLM 模型清单并生成 `litellm_model_names`
- 从包中发现继承 `ToolInterface` 的工具类
- 将 `BaseTool` 适配为 `ToolInterface` 并生成动态输入模型

关键组件：
- `get_patched_openai_client`：复用补丁客户端并写入缓存
- `tools_from_package`：扫描包内工具类
- `wrap_base_tool_as_tool_interface`：动态 schema -> 工具适配

设计背景：Astra Assistants 需要统一工具契约与输入 schema，且与 LangChain 生态兼容
使用场景：服务启动时初始化工具、运行时将外部工具接入 Assistants
注意事项：本模块不记录日志；网络拉取失败会回落到空集合
"""

import importlib
import inspect
import json
import os
import pkgutil
import threading
import uuid
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Any

import astra_assistants.tools as astra_assistants_tools
import requests
from astra_assistants import OpenAIWithDefaultKey, patch
from astra_assistants.tools.tool_interface import ToolInterface
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from requests.exceptions import RequestException

from lfx.base.mcp.util import create_input_schema_from_json_schema
from lfx.services.cache.utils import CacheMiss

client_lock = threading.Lock()
client = None


def get_patched_openai_client(shared_component_cache):
    """返回已补丁的 OpenAI 客户端并复用缓存。

    契约：入参 `shared_component_cache` 需有 `get/set`；返回补丁后的 client。
    副作用：设置环境变量 `ASTRA_ASSISTANTS_QUIET`，并可能写入缓存。
    失败语义：`get/set` 或 `patch()` 异常将原样抛出，调用方负责降级/重试。
    关键路径：1) 静默开关 2) 取缓存 3) Miss 时创建并写回。
    决策：按缓存 Miss 才创建补丁客户端。
    问题：避免每次调用重复 patch 导致开销/副作用。
    方案：使用共享缓存复用同一实例。
    代价：缓存生命周期内无法动态切换 key/config。
    重评：需要多租户或运行时配置切换时。
    """
    os.environ["ASTRA_ASSISTANTS_QUIET"] = "true"
    client = shared_component_cache.get("client")
    if isinstance(client, CacheMiss):
        client = patch(OpenAIWithDefaultKey())
        shared_component_cache.set("client", client)
    return client


url = "https://raw.githubusercontent.com/BerriAI/litellm/refs/heads/main/model_prices_and_context_window.json"
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = json.loads(response.text)
except RequestException:
    data = {}
except JSONDecodeError:
    data = {}

litellm_model_names = [model for model in data if model != "sample_spec"]


tool_names = []
tools_and_names = {}


def tools_from_package(your_package) -> None:
    """扫描包内模块并注册 `ToolInterface` 子类。

    契约：入参需为可迭代 `__path__` 的包对象；无返回值。
    副作用：更新全局 `tool_names` 与 `tools_and_names`。
    失败语义：导入模块或反射异常将原样抛出；调用方需捕获并隔离坏模块。
    关键路径：1) 枚举模块 2) 动态导入 3) 收集子类。
    决策：运行时扫描而非静态注册。
    问题：工具类频繁增删，手工注册易遗漏。
    方案：通过 `pkgutil.iter_modules` + 反射收集。
    代价：启动时导入成本上升，且会执行模块顶层代码。
    重评：当启动时间或副作用不可接受时。
    """
    package_name = your_package.__name__
    for module_info in pkgutil.iter_modules(your_package.__path__):
        module_name = f"{package_name}.{module_info.name}"

        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
            )
            warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
            module = importlib.import_module(module_name)

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, ToolInterface) and obj is not ToolInterface:
                tool_names.append(name)
                tools_and_names[name] = obj


tools_from_package(astra_assistants_tools)


def wrap_base_tool_as_tool_interface(base_tool: BaseTool) -> ToolInterface:
    """将 LangChain `BaseTool` 适配为 `ToolInterface`。

    契约：入参为 `BaseTool` 实例；返回可调用的 `ToolInterface` 实例。
    副作用：创建运行时 Pydantic 输入模型并生成包装类。
    失败语义：`args_schema` 类型不受支持时抛 `TypeError`；其余异常原样抛出。
    关键路径（三步）：
    1) 解析 `args_schema`（函数/模型/字典/空）。
    2) 转为 JSON schema 并动态创建 `InputSchema`。
    3) 生成包装类，将 `invoke/run` 结果映射为 Assistants 输出结构。
    异常流：`args_schema` 类型错误、`BaseTool.invoke` 内部异常。
    性能瓶颈：动态 schema 构建与模型校验。
    排障入口：无内置日志，依赖异常堆栈定位。
    决策：只在 `args_schema` 为函数/方法时调用它。
    问题：Pydantic 模型类可调用，误调用会实例化并丢失 schema。
    方案：用 `inspect.isfunction/ismethod` 进行区分。
    代价：无法支持其它可调用对象作为 schema 工厂。
    重评：若上游引入标准化 schema 工厂接口。
    """
    raw_args_schema = getattr(base_tool, "args_schema", None)

    # 注意：仅在确认是函数/方法时调用，避免误调用 Pydantic 模型类。
    if inspect.isfunction(raw_args_schema) or inspect.ismethod(raw_args_schema):
        raw_args_schema = raw_args_schema()

    if raw_args_schema is None:
        schema_dict = {"type": "object", "properties": {}}
    elif isinstance(raw_args_schema, dict):
        schema_dict = raw_args_schema
    elif inspect.isclass(raw_args_schema) and issubclass(raw_args_schema, BaseModel):
        schema_dict = raw_args_schema.schema()
    else:
        msg = f"args_schema must be a Pydantic model class, a JSON schema dict, or None. Got: {raw_args_schema!r}"
        raise TypeError(msg)

    InputSchema: type[BaseModel] = create_input_schema_from_json_schema(schema_dict)  # noqa: N806

    class WrappedDynamicTool(ToolInterface):
        """适配后的工具包装器。

        契约：`call` 接收 `InputSchema` 并返回 Assistants 输出结构。
        副作用：调用底层 `BaseTool.invoke/run`。
        失败语义：底层异常不吞并，直接抛出。
        """

        def __init__(self, tool: BaseTool):
            self._tool = tool

        def call(self, arguments: InputSchema) -> dict:  # type: ignore # noqa: PGH003
            output = self._tool.invoke(arguments.dict())  # type: ignore # noqa: PGH003
            result = ""
            if "error" in output[0].data:
                result = output[0].data["error"]
            elif "result" in output[0].data:
                result = output[0].data["result"]
            return {"cache_id": str(uuid.uuid4()), "output": result}

        def run(self, tool_input: Any) -> str:
            return self._tool.run(tool_input)

        def name(self) -> str:
            """优先返回底层工具名称。"""
            if hasattr(self._tool, "name"):
                return str(self._tool.name)
            return super().name()

        def to_function(self):
            """将底层工具描述合并进 OpenAI function schema。"""
            params = InputSchema.schema()
            description = getattr(self._tool, "description", "A dynamically wrapped tool")
            return {
                "type": "function",
                "function": {"name": self.name(), "description": description, "parameters": params},
            }

    return WrappedDynamicTool(base_tool)


def sync_upload(file_path, client):
    """同步上传文件到 Assistants 文件接口。

    契约：入参 `file_path` 为可读文件路径，`client` 需提供 `files.create`。
    副作用：读取本地文件并发起网络上传请求。
    失败语义：文件 I/O 或网络异常将原样抛出，调用方负责处理。
    关键路径：1) 打开文件 2) 调用上传 3) 返回服务响应。
    决策：使用同步文件句柄上传而非读入内存。
    问题：大文件读入内存会占用高峰内存。
    方案：以流式文件句柄交给 SDK 处理。
    代价：上传期间阻塞当前线程。
    重评：需要并发上传或异步 IO 时。
    """
    with Path(file_path).open("rb") as sync_file_handle:
        return client.files.create(
            file=sync_file_handle,
            purpose="assistants",
        )
