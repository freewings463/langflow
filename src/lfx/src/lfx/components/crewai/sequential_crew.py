"""
模块名称：CrewAI 顺序协作组件

本模块提供顺序执行模式的 Crew 组件封装，支持按照任务顺序执行。
主要功能包括：
- 从任务中派生 Agent 列表
- 构建 `Crew(process=sequential)` 实例

关键组件：
- `SequentialCrewComponent`：顺序协作 Crew 组件入口

设计背景：适配 CrewAI 的顺序任务执行模式。
注意事项：Agent 列表由任务对象派生，不建议重复传入。
"""

from lfx.base.agents.crewai.crew import BaseCrewComponent
from lfx.io import HandleInput
from lfx.schema.message import Message


class SequentialCrewComponent(BaseCrewComponent):
    display_name: str = "Sequential Crew"
    description: str = "Represents a group of agents with tasks that are executed sequentially."
    documentation: str = "https://docs.crewai.com/how-to/Sequential/"
    icon = "CrewAI"
    legacy = True
    replacement = "agents.Agent"

    inputs = [
        *BaseCrewComponent.get_base_inputs(),
        HandleInput(name="tasks", display_name="Tasks", input_types=["SequentialTask"], is_list=True),
    ]

    @property
    def agents(self: "SequentialCrewComponent") -> list:
        """从任务中派生 Agent 列表。

        契约：输出任务中关联的 Agent 列表（过滤缺失）。
        副作用：无。
        失败语义：任务缺失 `agent` 属性时会被跳过。
        """
        return [task.agent for task in self.tasks if hasattr(task, "agent")]

    def get_tasks_and_agents(self, agents_list=None) -> tuple[list, list]:
        """获取任务与 Agent 列表并合并外部传入。

        契约：输出 `(tasks, agents)`，其中 agents 含任务派生与外部补充。
        副作用：无。
        失败语义：无。
        """
        if not agents_list:
            existing_agents = self.agents
            agents_list = existing_agents + (agents_list or [])

        return super().get_tasks_and_agents(agents_list=agents_list)

    def build_crew(self) -> Message:
        """构建顺序协作 Crew。

        契约：输出 `Crew` 实例，执行模式为 `Process.sequential`。
        关键路径（三步）：
        1) 获取任务与 Agent 列表。
        2) 构建顺序执行 Crew。
        3) 返回可运行实例。

        异常流：依赖缺失抛 `ImportError`。
        """
        try:
            from crewai import Crew, Process
        except ImportError as e:
            msg = "CrewAI is not installed. Please install it with `uv pip install crewai`."
            raise ImportError(msg) from e

        tasks, agents = self.get_tasks_and_agents()

        return Crew(
            agents=agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=self.verbose,
            memory=self.memory,
            cache=self.use_cache,
            max_rpm=self.max_rpm,
            share_crew=self.share_crew,
            function_calling_llm=self.function_calling_llm,
            step_callback=self.get_step_callback(),
            task_callback=self.get_task_callback(),
        )
