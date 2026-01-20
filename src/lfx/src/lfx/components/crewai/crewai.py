"""
模块名称：CrewAI Agent 组件

本模块提供 CrewAI Agent 的组件封装，支持在 Langflow 中配置角色、目标与工具并输出
可运行的 Agent 实例。主要功能包括：
- 将 Langflow 的 LLM/Tool 适配到 CrewAI
- 构建 Agent 并暴露为组件输出

关键组件：
- `CrewAIAgentComponent`：Agent 构建组件入口

设计背景：对 CrewAI Agent 做统一的组件封装，便于在流程中复用。
注意事项：运行依赖 `crewai` 包，未安装将抛 `ImportError`。
"""

from lfx.base.agents.crewai.crew import convert_llm, convert_tools
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DictInput, HandleInput, MultilineInput, Output


class CrewAIAgentComponent(Component):
    """CrewAI Agent 组件。

    契约：输入角色/目标/工具/LLM 配置，输出 CrewAI `Agent` 实例。
    副作用：构建 Agent 时会进行工具与 LLM 适配。
    失败语义：缺少 `crewai` 依赖时抛 `ImportError`。
    """

    display_name = "CrewAI Agent"
    description = "Represents an agent of CrewAI."
    documentation: str = "https://docs.crewai.com/how-to/LLM-Connections/"
    icon = "CrewAI"
    legacy = True
    replacement = "agents.Agent"

    inputs = [
        MultilineInput(name="role", display_name="Role", info="The role of the agent."),
        MultilineInput(name="goal", display_name="Goal", info="The objective of the agent."),
        MultilineInput(name="backstory", display_name="Backstory", info="The backstory of the agent."),
        HandleInput(
            name="tools",
            display_name="Tools",
            input_types=["Tool"],
            is_list=True,
            info="Tools at agents disposal",
            value=[],
        ),
        HandleInput(
            name="llm",
            display_name="Language Model",
            info="Language model that will run the agent.",
            input_types=["LanguageModel"],
        ),
        BoolInput(
            name="memory",
            display_name="Memory",
            info="Whether the agent should have memory or not",
            advanced=True,
            value=True,
        ),
        BoolInput(
            name="verbose",
            display_name="Verbose",
            advanced=True,
            value=False,
        ),
        BoolInput(
            name="allow_delegation",
            display_name="Allow Delegation",
            info="Whether the agent is allowed to delegate tasks to other agents.",
            value=True,
        ),
        BoolInput(
            name="allow_code_execution",
            display_name="Allow Code Execution",
            info="Whether the agent is allowed to execute code.",
            value=False,
            advanced=True,
        ),
        DictInput(
            name="kwargs",
            display_name="kwargs",
            info="kwargs of agent.",
            is_list=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Agent", name="output", method="build_output"),
    ]

    def build_output(self):
        """构建 CrewAI Agent 实例。

        契约：输出 `Agent`，并将其可读表示写入 `self.status`。
        关键路径（三步）：
        1) 校验依赖并读取组件输入。
        2) 适配 LLM 与工具集合。
        3) 生成 Agent 并返回。

        异常流：依赖缺失抛 `ImportError`。
        """
        try:
            from crewai import Agent
        except ImportError as e:
            msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
            raise ImportError(msg) from e

        kwargs = self.kwargs or {}

        agent = Agent(
            role=self.role,
            goal=self.goal,
            backstory=self.backstory,
            llm=convert_llm(self.llm),
            verbose=self.verbose,
            memory=self.memory,
            tools=convert_tools(self.tools),
            allow_delegation=self.allow_delegation,
            allow_code_execution=self.allow_code_execution,
            **kwargs,
        )

        self.status = repr(agent)

        return agent
