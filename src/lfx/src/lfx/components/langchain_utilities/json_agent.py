"""模块名称：JSON/YAML 代理组件

本模块封装 LangChain JSON 代理构建逻辑，支持本地文件与 S3 存储的透明读取。
主要功能包括：解析 JSON/YAML、构建 `JsonToolkit`、创建代理执行器。

关键组件：
- `JsonAgentComponent`：JSON 代理的组件化入口

设计背景：在流程中统一结构化配置文件的查询方式。
注意事项：S3 文件会被下载到临时路径并在构建后清理。
"""

import contextlib
import tempfile
from pathlib import Path

import yaml
from langchain.agents import AgentExecutor

from lfx.base.agents.agent import LCAgentComponent
from lfx.base.data.storage_utils import read_file_bytes
from lfx.inputs.inputs import FileInput, HandleInput
from lfx.services.deps import get_settings_service
from lfx.utils.async_helpers import run_until_complete


class JsonAgentComponent(LCAgentComponent):
    """JSON/YAML 代理组件。

    契约：输入 `llm/path`；输出 `AgentExecutor`；副作用：可能创建临时文件；
    失败语义：依赖缺失抛 `ImportError`，解析失败抛原异常。
    关键路径：1) 获取本地路径 2) 解析 JSON/YAML 构建 `JsonSpec` 3) 创建代理。
    决策：优先通过 `JsonSpec` 统一处理 YAML/JSON
    问题：不同格式需要统一接口
    方案：YAML 先解析为 dict 再包装为 `JsonSpec`
    代价：YAML 解析失败会阻断代理构建
    重评：当支持更多格式时扩展解析分支
    """
    display_name = "JsonAgent"
    description = "Construct a json agent from an LLM and tools."
    name = "JsonAgent"
    legacy: bool = True

    inputs = [
        *LCAgentComponent.get_base_inputs(),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
        ),
        FileInput(
            name="path",
            display_name="File Path",
            file_types=["json", "yaml", "yml"],
            required=True,
        ),
    ]

    def _get_local_path(self) -> Path:
        """获取本地路径，必要时从 S3 下载。

        契约：输入 `self.path`；输出 `Path`；副作用：可能写临时文件；
        失败语义：S3 读取失败会向上抛出。
        关键路径：1) 判断存储类型 2) S3 下载 3) 返回临时路径。
        决策：使用临时文件而非内存
        问题：LangChain JSON 代理要求文件路径
        方案：落盘后交给 `JsonSpec` 读取
        代价：磁盘占用与清理成本
        重评：当代理支持文件对象时切换为内存流
        """
        file_path = self.path
        settings = get_settings_service().settings

        # 使用 S3 存储时先下载到临时文件
        if settings.storage_type == "s3":
            # 从 S3 下载到临时文件
            file_bytes = run_until_complete(read_file_bytes(file_path))

            # 创建带合适后缀的临时文件
            suffix = Path(file_path.split("/")[-1]).suffix or ".json"
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(file_bytes)
                temp_path = tmp_file.name

            # 保存临时路径便于清理
            self._temp_file_path = temp_path
            return Path(temp_path)

        # 本地存储直接返回路径
        return Path(file_path)

    def _cleanup_temp_file(self) -> None:
        """清理临时文件（若存在）。

        契约：输入无；输出无；副作用：删除临时文件；失败语义：异常被抑制以避免影响主流程。
        关键路径：1) 判断 `_temp_file_path` 2) 容错删除。
        决策：忽略删除异常
        问题：清理失败不应阻断构建
        方案：`contextlib.suppress` 包裹
        代价：可能遗留临时文件
        重评：当需要强一致清理时改为显式告警
        """
        if hasattr(self, "_temp_file_path"):
            with contextlib.suppress(Exception):
                Path(self._temp_file_path).unlink()  # 忽略清理异常

    def build_agent(self) -> AgentExecutor:
        """构建 JSON/YAML 代理执行器。

        关键路径（三步）：
        1) 校验依赖并准备 `JsonToolkit`
        2) 解析文件生成 `JsonSpec`
        3) 创建代理并清理临时文件

        异常流：缺少 `langchain-community` 抛 `ImportError`；解析异常透传。
        排障入口：`ImportError` 文案含 `pip install langchain-community`。
        决策：无论成功/失败都尝试清理临时文件
        问题：S3 下载可能泄露临时文件
        方案：在 `except/else` 分支中清理
        代价：构建失败时仍需额外 I/O
        重评：当引入统一清理器时移除重复逻辑
        """
        try:
            from langchain_community.agent_toolkits import create_json_agent
            from langchain_community.agent_toolkits.json.toolkit import JsonToolkit
            from langchain_community.tools.json.tool import JsonSpec
        except ImportError as e:
            msg = "langchain-community is not installed. Please install it with `pip install langchain-community`."
            raise ImportError(msg) from e

        try:
            # 获取本地路径（必要时从 S3 下载）
            path = self._get_local_path()

            if path.suffix in {".yaml", ".yml"}:
                with path.open(encoding="utf-8") as file:
                    yaml_dict = yaml.safe_load(file)
                spec = JsonSpec(dict_=yaml_dict)
            else:
                spec = JsonSpec.from_file(str(path))
            toolkit = JsonToolkit(spec=spec)

            agent = create_json_agent(llm=self.llm, toolkit=toolkit, **self.get_agent_kwargs())
        except Exception:
            # 异常时确保清理临时文件
            self._cleanup_temp_file()
            raise
        else:
            # 成功创建后清理临时文件
            self._cleanup_temp_file()
            return agent
