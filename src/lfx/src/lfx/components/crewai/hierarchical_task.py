"""
模块名称：CrewAI 层级任务组件

本模块提供层级任务的组件封装，负责将任务描述与期望输出组合为 `HierarchicalTask`。
主要功能包括：
- 组装层级任务描述与期望输出
- 可选绑定任务级工具集合

关键组件：
- `HierarchicalTaskComponent`：层级任务组件入口

设计背景：配合层级 Crew 进行任务拆解与委派。
注意事项：任务工具为空时默认使用 Agent 工具集。
"""

from lfx.base.agents.crewai.tasks import HierarchicalTask
from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MultilineInput, Output


class HierarchicalTaskComponent(Component):
    display_name: str = "Hierarchical Task"
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
    ]

    outputs = [
        Output(display_name="Task", name="task_output", method="build_task"),
    ]

    def build_task(self) -> HierarchicalTask:
        """构建层级任务对象。

        契约：输入任务描述/期望输出/工具列表，输出 `HierarchicalTask`。
        副作用：设置 `self.status` 以便界面展示。
        失败语义：无显式异常，参数缺失由上游输入约束处理。
        """
        task = HierarchicalTask(
            description=self.task_description,
            expected_output=self.expected_output,
            tools=self.tools or [],
        )
        self.status = task
        return task
