"""
模块名称：CrewAI 层级协作组件

本模块提供层级协作模式的 Crew 组件封装，支持管理者 LLM/Agent 与多任务协作。
主要功能包括：
- 组装层级任务与 Agent 列表
- 构建 `Crew(process=hierarchical)` 实例

关键组件：
- `HierarchicalCrewComponent`：层级协作 Crew 组件入口

设计背景：支持 CrewAI 的层级流程编排模式。
注意事项：运行依赖 `crewai` 包，未安装将抛 `ImportError`。
"""

from lfx.base.agents.crewai.crew import BaseCrewComponent
from lfx.io import HandleInput


class HierarchicalCrewComponent(BaseCrewComponent):
    display_name: str = "Hierarchical Crew"
    description: str = (
        "Represents a group of agents, defining how they should collaborate and the tasks they should perform."
    )
    documentation: str = "https://docs.crewai.com/how-to/Hierarchical/"
    icon = "CrewAI"
    legacy = True
    replacement = "agents.Agent"

    inputs = [
        *BaseCrewComponent.get_base_inputs(),
        HandleInput(name="agents", display_name="Agents", input_types=["Agent"], is_list=True),
        HandleInput(name="tasks", display_name="Tasks", input_types=["HierarchicalTask"], is_list=True),
        HandleInput(name="manager_llm", display_name="Manager LLM", input_types=["LanguageModel"], required=False),
        HandleInput(name="manager_agent", display_name="Manager Agent", input_types=["Agent"], required=False),
    ]

    def build_crew(self):
        """构建层级协作 Crew。

        契约：输出 `Crew` 实例，执行模式为 `Process.hierarchical`。
        关键路径（三步）：
        1) 获取任务与 Agent 列表。
        2) 解析管理者 LLM 配置。
        3) 构建 Crew 并返回。

        异常流：依赖缺失抛 `ImportError`。
        """
        try:
            from crewai import Crew, Process
        except ImportError as e:
            msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
            raise ImportError(msg) from e

        tasks, agents = self.get_tasks_and_agents()
        manager_llm = self.get_manager_llm()

        return Crew(
            agents=agents,
            tasks=tasks,
            process=Process.hierarchical,
            verbose=self.verbose,
            memory=self.memory,
            cache=self.use_cache,
            max_rpm=self.max_rpm,
            share_crew=self.share_crew,
            function_calling_llm=self.function_calling_llm,
            manager_agent=self.manager_agent,
            manager_llm=manager_llm,
            step_callback=self.get_step_callback(),
            task_callback=self.get_task_callback(),
        )
