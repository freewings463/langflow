"""模块名称：CSV 代理组件

本模块封装 LangChain CSV 代理的构建与执行，支持本地文件与 S3 存储的透明读取。
主要功能包括：解析输入路径、构建 CSV Agent、执行查询并清理临时文件。

关键组件：
- `CSVAgentComponent`：CSV 代理的组件化入口

设计背景：统一 CSV 查询类工具的接入方式，避免在流程里手工处理临时文件。
注意事项：`allow_dangerous_code` 被开启；S3 文件会落地到临时目录并在使用后清理。
"""

import contextlib
import tempfile
from pathlib import Path

from lfx.base.agents.agent import LCAgentComponent
from lfx.base.data.storage_utils import read_file_bytes
from lfx.field_typing import AgentExecutor
from lfx.inputs.inputs import (
    DictInput,
    DropdownInput,
    FileInput,
    HandleInput,
    MessageTextInput,
)
from lfx.schema.message import Message
from lfx.services.deps import get_settings_service
from lfx.template.field.base import Output
from lfx.utils.async_helpers import run_until_complete


class CSVAgentComponent(LCAgentComponent):
    """CSV 代理组件。

    契约：输入 `llm/path/input_value/agent_type/pandas_kwargs`；输出 `Message` 或 `AgentExecutor`；
    副作用：可能创建临时文件并更新 `self.status`；失败语义：依赖缺失抛 `ImportError`。
    关键路径：1) 解析路径与存储类型 2) 构建 CSV Agent 3) 执行或返回代理。
    决策：本地化 S3 文件再交给 LangChain
    问题：LangChain 仅接受本地路径
    方案：下载到临时文件并缓存路径
    代价：额外 I/O 与磁盘占用
    重评：当 LangChain 支持文件对象或流式读取时去掉落盘
    """
    display_name = "CSV Agent"
    description = "Construct a CSV agent from a CSV and tools."
    documentation = "https://python.langchain.com/docs/modules/agents/toolkits/csv"
    name = "CSVAgent"
    icon = "LangChain"

    inputs = [
        *LCAgentComponent.get_base_inputs(),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
            info="An LLM Model Object (It can be found in any LLM Component).",
        ),
        FileInput(
            name="path",
            display_name="File Path",
            file_types=["csv"],
            input_types=["str", "Message"],
            required=True,
            info="A CSV File or File Path.",
        ),
        DropdownInput(
            name="agent_type",
            display_name="Agent Type",
            advanced=True,
            options=["zero-shot-react-description", "openai-functions", "openai-tools"],
            value="openai-tools",
        ),
        MessageTextInput(
            name="input_value",
            display_name="Text",
            info="Text to be passed as input and extract info from the CSV File.",
            required=True,
        ),
        DictInput(
            name="pandas_kwargs",
            display_name="Pandas Kwargs",
            info="Pandas Kwargs to be passed to the agent.",
            advanced=True,
            is_list=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="build_agent_response"),
        Output(display_name="Agent", name="agent", method="build_agent", hidden=True, tool_mode=False),
    ]

    def _path(self) -> str:
        """统一 `path` 的输入形态。

        契约：输入 `self.path`；输出字符串路径；副作用无；失败语义：非字符串将原样返回。
        关键路径：1) 处理 `Message` 包装 2) 返回字符串或原值。
        决策：优先读取 `Message.text`
        问题：输入可能来自上游消息节点
        方案：当 `Message.text` 为字符串时直接使用
        代价：丢失 `Message` 其他字段
        重评：当需要保留元数据时返回对象并上移解析
        """
        if isinstance(self.path, Message) and isinstance(self.path.text, str):
            return self.path.text
        return self.path

    def build_agent_response(self) -> Message:
        """构建并执行 CSV Agent，返回响应消息。

        关键路径（三步）：
        1) 校验依赖并准备 `agent_kwargs`
        2) 获取本地路径并构建 agent 执行
        3) finally 清理临时文件

        异常流：缺少 `langchain-experimental` 抛 `ImportError`；执行异常透传。
        性能瓶颈：CSV 解析与 LLM 调用。
        排障入口：`ImportError` 文案含 `pip install langchain-experimental`。
        决策：`allow_dangerous_code=True`
        问题：CSV 代理内部需执行生成代码
        方案：显式允许以避免工具不可用
        代价：执行不可信代码风险上升
        重评：当引入沙箱执行时默认关闭
        """
        try:
            from langchain_experimental.agents.agent_toolkits.csv.base import create_csv_agent
        except ImportError as e:
            msg = (
                "langchain-experimental is not installed. Please install it with `pip install langchain-experimental`."
            )
            raise ImportError(msg) from e

        try:
            agent_kwargs = {
                "verbose": self.verbose,
                "allow_dangerous_code": True,
            }

            # 获取本地路径（必要时从 S3 下载）
            local_path = self._get_local_path()

            agent_csv = create_csv_agent(
                llm=self.llm,
                path=local_path,
                agent_type=self.agent_type,
                handle_parsing_errors=self.handle_parsing_errors,
                pandas_kwargs=self.pandas_kwargs,
                **agent_kwargs,
            )

            result = agent_csv.invoke({"input": self.input_value})
            return Message(text=str(result["output"]))

        finally:
            # 清理可能创建的临时文件
            self._cleanup_temp_file()

    def build_agent(self) -> AgentExecutor:
        """仅构建 CSV Agent 执行器。

        契约：输入 `llm/path/agent_type/pandas_kwargs`；输出 `AgentExecutor`；
        副作用：可能创建临时文件并写入 `self.status`；失败语义：依赖缺失抛 `ImportError`。
        关键路径：1) 校验依赖 2) 获取本地路径 3) 构建 agent 并返回。
        决策：不在此处清理临时文件
        问题：调用方可能希望复用 agent
        方案：交由组件生命周期或 `build_agent_response` 统一清理
        代价：临时文件存续时间更长
        重评：当代理改为一次性执行时立即清理
        """
        try:
            from langchain_experimental.agents.agent_toolkits.csv.base import create_csv_agent
        except ImportError as e:
            msg = (
                "langchain-experimental is not installed. Please install it with `pip install langchain-experimental`."
            )
            raise ImportError(msg) from e

        agent_kwargs = {
            "verbose": self.verbose,
            "allow_dangerous_code": True,
        }

        # 获取本地路径（必要时从 S3 下载）
        local_path = self._get_local_path()

        agent_csv = create_csv_agent(
            llm=self.llm,
            path=local_path,
            agent_type=self.agent_type,
            handle_parsing_errors=self.handle_parsing_errors,
            pandas_kwargs=self.pandas_kwargs,
            **agent_kwargs,
        )

        self.status = Message(text=str(agent_csv))

        # 注意：临时文件会在组件销毁或调用 `build_agent_response` 时清理
        return agent_csv

    def _get_local_path(self) -> str:
        """获取本地可用路径，必要时从 S3 下载。

        契约：输入 `self.path`；输出本地路径字符串；副作用：可能写临时文件；
        失败语义：S3 读取异常会向上抛出。
        关键路径：1) 判断存储类型 2) S3 下载并落盘 3) 返回本地路径。
        决策：使用临时文件而非内存缓冲
        问题：LangChain CSV 代理需要文件路径
        方案：`NamedTemporaryFile(delete=False)` 落盘
        代价：磁盘占用与清理成本
        重评：当 LangChain 支持文件对象时切换为内存流
        """
        file_path = self._path()
        settings = get_settings_service().settings

        # 使用 S3 存储时先下载到临时文件
        if settings.storage_type == "s3":
            # 从 S3 下载到临时文件
            csv_bytes = run_until_complete(read_file_bytes(file_path))

            # 创建带 `.csv` 后缀的临时文件
            suffix = Path(file_path.split("/")[-1]).suffix or ".csv"
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(csv_bytes)
                temp_path = tmp_file.name

            # 保存临时路径便于清理
            self._temp_file_path = temp_path
            return temp_path

        # 本地存储直接返回路径
        return file_path

    def _cleanup_temp_file(self) -> None:
        """清理临时文件（若存在）。

        契约：输入无；输出无；副作用：删除临时文件；失败语义：异常被抑制以避免影响主流程。
        关键路径：1) 判断 `_temp_file_path` 2) 容错删除。
        决策：忽略删除异常
        问题：清理失败不应阻断主链路
        方案：`contextlib.suppress` 包裹
        代价：可能遗留垃圾文件
        重评：当需要强一致清理时改为显式告警
        """
        if hasattr(self, "_temp_file_path"):
            with contextlib.suppress(Exception):
                Path(self._temp_file_path).unlink()  # 忽略清理异常
