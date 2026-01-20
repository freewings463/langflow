"""
模块名称：顺序任务组件

本模块提供顺序任务的组件封装，用于创建 `SequentialTask` 并与已有任务链拼接。
主要功能包括：
- 构建顺序任务并绑定 Agent
- 追加或合并已有任务链

关键组件：
- `SequentialTaskComponent`：顺序任务组件入口

设计背景：在顺序执行模式中复用统一的任务构建逻辑。
注意事项：默认使用 Agent 自身的工具集。
"""

from lfx.base.agents.crewai.tasks import SequentialTask
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, HandleInput, MultilineInput, Output


class SequentialTaskComponent(Component):
    display_name: str = "Sequential Task"
    description: str = "Each task must have a description, an expected output and an agent responsible for execution."
    icon = "CrewAI"
    legacy = True
    replacement = "agents.Agent"
    inputs = [
        MultilineInput(
            name="task_description",
            display_name="Description",
            info="Descriptive text detailing task's purpose and execution.",
        ),
        MultilineInput(
            name="expected_output",
            display_name="Expected Output",
            info="Clear definition of expected task outcome.",
        ),
        HandleInput(
            name="tools",
            display_name="Tools",
            input_types=["Tool"],
            is_list=True,
            info="List of tools/resources limited for task execution. Uses the Agent tools by default.",
            required=False,
            advanced=True,
        ),
        HandleInput(
            name="agent",
            display_name="Agent",
            input_types=["Agent"],
            info="CrewAI Agent that will perform the task",
            required=True,
        ),
        HandleInput(
            name="task",
            display_name="Task",
            input_types=["SequentialTask"],
            info="CrewAI Task that will perform the task",
        ),
        BoolInput(
            name="async_execution",
            display_name="Async Execution",
            value=True,
            advanced=True,
            info="Boolean flag indicating asynchronous task execution.",
        ),
    ]

    outputs = [
        Output(display_name="Task", name="task_output", method="build_task"),
    ]

    def build_task(self) -> list[SequentialTask]:
        """构建顺序任务并返回任务链。

        契约：输出 `SequentialTask` 列表，包含新建任务与可选已有任务。
        关键路径（三步）：
        1) 构建当前任务并绑定 Agent。
        2) 合并传入任务链（如有）。
        3) 返回最终任务列表。

        失败语义：无显式异常，输入约束由上游处理。
        """
        tasks: list[SequentialTask] = []
        task = SequentialTask(
            description=self.task_description,
            expected_output=self.expected_output,
            tools=self.agent.tools,
            async_execution=False,
            agent=self.agent,
        )
        tasks.append(task)
        self.status = task
        if self.task:
            if isinstance(self.task, list) and all(isinstance(task_item, SequentialTask) for task_item in self.task):
                tasks = self.task + tasks
            elif isinstance(self.task, SequentialTask):
                tasks = [self.task, *tasks]
        return tasks
