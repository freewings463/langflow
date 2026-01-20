"""
模块名称：`CrewAI` 适配器

本模块提供将 `LangChain` 组件（`LLM`、工具、代理）转换为 `CrewAI` 兼容格式的能力，主要用于在
`LangFlow` 中集成 `CrewAI` 协作智能体。
主要功能包括：
- `convert_llm`：转换 `LangChain` `LLM` 为 `CrewAI` `LLM`
- `convert_tools`：转换 `LangChain` 工具为 `CrewAI` 工具
- `BaseCrewComponent`：CrewAI 组件基类与生命周期管理

关键组件：
- `BaseCrewComponent`、`convert_llm`、`convert_tools`

设计背景：`CrewAI` 与 `LangChain` 的模型/工具协议不同，需要统一适配层。
注意事项：`CrewAI` 为可选依赖，缺失时会抛 `ImportError`。
"""

from collections.abc import Callable
from typing import Any, cast

import litellm
from pydantic import SecretStr

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput, InputTypes
from lfx.io import BoolInput, IntInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.utils.constants import MESSAGE_SENDER_AI


def _find_api_key(model):
    """从 LangChain LLM 实例中提取 API Key。

    契约：入参 `model`；返回 `str|None`；副作用：无。
    失败语义：未匹配到 `key`/`token` 字段则返回 `None`。
    关键路径：1) 遍历 `dir(model)`；2) 匹配含 `key`/`token` 的属性名；3) 支持 `SecretStr` 解密。
    决策：允许“包含匹配”而非固定字段
    问题：不同 LLM 的 API Key 字段名不一致
    方案：用 `key`/`token` 片段匹配并读取字符串或 `SecretStr`
    代价：可能命中无关字段，需调用方校验
    重评：当主流 LLM 统一字段名或出现误匹配时
    """
    key_patterns = ["key", "token"]

    for attr in dir(model):
        attr_lower = attr.lower()

        if any(pattern in attr_lower for pattern in key_patterns):
            value = getattr(model, attr, None)

            if isinstance(value, str):
                return value
            if isinstance(value, SecretStr):
                return value.get_secret_value()

    return None


def convert_llm(llm: Any, excluded_keys=None):
    """将 LangChain LLM 转为 CrewAI `LLM`。

    契约：入参 `llm`/`excluded_keys`；返回 CrewAI `LLM` 或 `None`；副作用：无。
    失败语义：未安装 crewai 抛 `ImportError`；缺少模型名抛 `ValueError`。
    关键路径：1) 解析 `model_name/model/deployment_name`；2) 规范化 provider 与 `azure` 前缀；3) 组装 `LLM` 并排除字段。
    决策：从 `llm.dict()` 透传配置并排除敏感字段
    问题：不同 LLM 参数集不一致，需尽量保留可用配置
    方案：黑名单排除 `model`/`api_key` 等后批量透传
    代价：依赖 `dict()` 结构稳定，可能漏传新字段
    重评：CrewAI/LC 参数结构发生变化时
    """
    try:
        from crewai import LLM
    except ImportError as e:
        msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
        raise ImportError(msg) from e

    if not llm:
        return None

    if isinstance(llm, LLM):
        return llm

    if hasattr(llm, "model_name") and llm.model_name:
        model_name = llm.model_name
    elif hasattr(llm, "model") and llm.model:
        model_name = llm.model
    elif hasattr(llm, "deployment_name") and llm.deployment_name:
        model_name = llm.deployment_name
    else:
        msg = "Could not find model name in the LLM object"
        raise ValueError(msg)

    provider = llm.get_lc_namespace()[0]
    api_base = None
    if provider.startswith("langchain_"):
        provider = provider[10:]
        model_name = f"{provider}/{model_name}"
    elif hasattr(llm, "azure_endpoint"):
        api_base = llm.azure_endpoint
        model_name = f"azure/{model_name}"

    if excluded_keys is None:
        excluded_keys = {"model", "model_name", "_type", "api_key", "azure_deployment"}

    api_key = _find_api_key(llm)

    return LLM(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
        **{k: v for k, v in llm.dict().items() if k not in excluded_keys},
    )


def convert_tools(tools):
    """将 LangChain 工具列表转为 CrewAI 工具。

    契约：入参 `tools` 列表或 `None`；返回 CrewAI 工具列表；副作用：无。
    失败语义：未安装 crewai 抛 `ImportError`；空输入返回 `[]`。
    关键路径：1) 校验依赖；2) 空列表快速返回；3) `Tool.from_langchain` 批量转换。
    决策：使用官方转换入口而非手写映射
    问题：LangChain 工具协议易变
    方案：调用 `Tool.from_langchain`
    代价：受 CrewAI 版本兼容性限制
    重评：当 CrewAI 提供更稳定的适配层时
    """
    try:
        from crewai.tools.base_tool import Tool
    except ImportError as e:
        msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
        raise ImportError(msg) from e

    if not tools:
        return []

    return [Tool.from_langchain(tool) for tool in tools]


class BaseCrewComponent(Component):
    """CrewAI 组件基类：组装 crew/agent/task 与回调。

    契约：子类实现 `build_crew()`；`build_output()` 异步返回 `Message`。
    副作用：记录 task/step 日志并更新 `self.status`。
    失败语义：`build_crew` 未实现抛 `NotImplementedError`；下游 LLM 错误透传。
    关键路径：1) 组装 agents/tasks；2) 构建 crew；3) kickoff 并封装消息。
    决策：以“可选依赖”的方式导入 crewai/langchain
    问题：避免在未安装依赖时阻断加载
    方案：方法内延迟导入并显式报错
    代价：错误发现更晚，运行时才抛 `ImportError`
    重评：当 crewai 成为强依赖时移除延迟导入
    """
    description: str = (
        "Represents a group of agents, defining how they should collaborate and the tasks they should perform."
    )
    icon = "CrewAI"

    _base_inputs: list[InputTypes] = [
        IntInput(name="verbose", display_name="Verbose", value=0, advanced=True),
        BoolInput(name="memory", display_name="Memory", value=False, advanced=True),
        BoolInput(name="use_cache", display_name="Cache", value=True, advanced=True),
        IntInput(name="max_rpm", display_name="Max RPM", value=100, advanced=True),
        BoolInput(name="share_crew", display_name="Share Crew", value=False, advanced=True),
        HandleInput(
            name="function_calling_llm",
            display_name="Function Calling LLM",
            input_types=["LanguageModel"],
            info="Turns the ReAct CrewAI agent into a function-calling agent",
            required=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="build_output"),
    ]

    manager_llm = None

    def task_is_valid(self, task_data: Data, crew_type) -> bool:
        """判断任务数据是否匹配 crew 类型。

        契约：入参 `task_data`/`crew_type`；返回 `bool`；副作用：无。
        失败语义：缺少 `task_type` 字段则返回 `False`。
        关键路径：1) 检查 `task_type` 是否存在；2) 与 `crew_type` 比较；3) 返回布尔。
        决策：只依赖 `task_type` 字段
        问题：任务数据结构不稳定且可能缺字段
        方案：最小字段校验避免硬绑定
        代价：可能放过结构不完整的数据
        重评：当任务 schema 固化并强校验时
        """
        return "task_type" in task_data and task_data.task_type == crew_type

    def get_tasks_and_agents(self, agents_list=None) -> tuple[list, list]:
        """获取任务列表与可用代理，并统一 LLM/工具格式。

        契约：入参 `agents_list` 可选；返回 `(tasks, agents)`；副作用：会原地修改 `agent.llm/tools`。
        失败语义：单个 agent 转换失败会向上抛异常。
        关键路径：1) 选用传入列表或 `self.agents`；2) 转换每个 agent 的 LLM/Tools；3) 返回任务与代理。
        决策：在组装阶段统一适配 LLM/工具
        问题：LangChain/CrewAI 对象协议不一致
        方案：调用 `convert_llm`/`convert_tools` 统一到 CrewAI
        代价：会改变原 agent 引用，影响后续复用
        重评：当引入不可变 agent 配置或支持双协议时
        """
        if not agents_list:
            agents_list = self.agents or []

        for agent in agents_list:
            agent.llm = convert_llm(agent.llm)
            agent.tools = convert_tools(agent.tools)

        return self.tasks, agents_list

    def get_manager_llm(self):
        """获取并转换 manager LLM。

        契约：无入参；返回 CrewAI `LLM` 或 `None`；副作用：可能更新 `self.manager_llm`。
        失败语义：转换失败时向上抛异常。
        关键路径：1) 为空直接返回；2) 调用 `convert_llm` 转换；3) 返回缓存结果。
        决策：缓存转换后的 `manager_llm`
        问题：重复转换会增加开销
        方案：在首次调用时就地替换为 CrewAI 形式
        代价：丢失原始 LangChain 对象
        重评：当需要保留原对象做回退或多协议时
        """
        if not self.manager_llm:
            return None

        self.manager_llm = convert_llm(self.manager_llm)

        return self.manager_llm

    def build_crew(self):
        """构建 crew 实例的抽象入口。

        契约：无入参；返回 crew 实例；副作用：无。
        失败语义：基类直接抛 `NotImplementedError`。
        关键路径：1) 子类装配 agents/tasks；2) 创建 crew；3) 返回实例。
        决策：将构建逻辑下放到子类
        问题：不同 crew 类型装配逻辑差异大
        方案：基类仅定义接口
        代价：子类实现成本更高
        重评：当 crew 构建流程趋同并可抽象时
        """
        msg = "build_crew must be implemented in subclasses"
        raise NotImplementedError(msg)

    def get_task_callback(
        self,
    ) -> Callable:
        """生成 task 回调，用于记录任务输出。

        契约：无入参；返回回调函数；副作用：写日志 `self.log`。
        失败语义：未安装 crewai 抛 `ImportError`。
        关键路径：1) 延迟导入 `TaskOutput`；2) 构造回调；3) 日志记录 `model_dump`。
        决策：使用 `model_dump` 统一序列化输出
        问题：任务输出对象可能包含非 JSON 字段
        方案：调用 Pydantic 序列化入口
        代价：丢失部分自定义对象细节
        重评：当需要完整对象回放或自定义序列化时
        """
        try:
            from crewai.task import TaskOutput
        except ImportError as e:
            msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
            raise ImportError(msg) from e

        def task_callback(task_output: TaskOutput) -> None:
            vertex_id = self._vertex.id if self._vertex else self.display_name or self.__class__.__name__
            self.log(task_output.model_dump(), name=f"Task (Agent: {task_output.agent}) - {vertex_id}")

        return task_callback

    def get_step_callback(
        self,
    ) -> Callable:
        """生成 step 回调，记录 agent 的中间动作与终态。

        契约：无入参；返回回调函数；副作用：写日志 `self.log`。
        失败语义：未安装 `langchain_core` 抛 `ImportError`。
        关键路径：1) 延迟导入 `AgentFinish`；2) 区分终态与动作列表；3) 序列化后记录。
        决策：对动作消息做显式 JSON 化
        问题：消息对象可能含循环引用导致日志失败
        方案：使用 `to_json()` 转为可序列化结构
        代价：日志不包含对象方法与运行态字段
        重评：当日志系统支持自定义编码器时
        """
        try:
            from langchain_core.agents import AgentFinish
        except ImportError as e:
            msg = "langchain_core is not installed. Please install it with `uv pip install langchain-core`."
            raise ImportError(msg) from e

        def step_callback(agent_output) -> None:
            id_ = self._vertex.id if self._vertex else self.display_name
            if isinstance(agent_output, AgentFinish):
                messages = agent_output.messages
                self.log(cast("dict", messages[0].to_json()), name=f"Finish (Agent: {id_})")
            elif isinstance(agent_output, list):
                messages_dict_ = {f"Action {i}": action.messages for i, (action, _) in enumerate(agent_output)}
                # 注意：使用 `to_json()` 规避消息中的循环引用导致日志序列化失败
                serializable_dict = {k: [m.to_json() for m in v] for k, v in messages_dict_.items()}
                messages_dict = {k: v[0] if len(v) == 1 else v for k, v in serializable_dict.items()}
                self.log(messages_dict, name=f"Step (Agent: {id_})")

        return step_callback

    async def build_output(self) -> Message:
        """执行 crew 并封装为消息输出。

        契约：无入参；返回 `Message`；副作用：调用 crew、更新 `self.status`。
        失败语义：`litellm` 的 `BadRequestError` 转为 `ValueError` 抛出。
        关键路径：1) 构建 crew；2) `kickoff_async` 获取结果；3) 封装 `Message` 并记录状态。
        决策：将第三方异常映射为 `ValueError`
        问题：上层组件依赖统一的错误类型
        方案：捕获 `BadRequestError` 并重新抛出
        代价：丢失原异常类型，需要从 `__cause__` 追溯
        重评：当上层支持保留原异常类型时
        """
        try:
            crew = self.build_crew()
            result = await crew.kickoff_async()
            message = Message(text=result.raw, sender=MESSAGE_SENDER_AI)
        except litellm.exceptions.BadRequestError as e:
            raise ValueError(e) from e

        self.status = message

        return message
