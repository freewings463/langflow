"""
模块名称：复杂层级 `Agent` 示例图

本模块构建多角色协作的层级 `Agent` 图，演示动态角色定义与层级任务调度。主要功能包括：
- 基于用户问题生成 `Role` / `Goal` / `Backstory`
- 由管理 `Agent` 统筹任务并调用子 `Agent` 工具

关键组件：
- `complex_agent_graph`: 构建层级任务 `Graph`

设计背景：展示多 `Agent` 分工协作与工具调用的完整链路。
注意事项：示例依赖外部工具与模型服务，运行时需配置相应凭据。
"""

from lfx.components.crewai.crewai import CrewAIAgentComponent
from lfx.components.crewai.hierarchical_crew import HierarchicalCrewComponent
from lfx.components.crewai.hierarchical_task import HierarchicalTaskComponent
from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.components.tools import SearchAPIComponent, YfinanceToolComponent
from lfx.graph import Graph


def complex_agent_graph():
    """构建包含动态角色与层级任务的 `Agent` 示例图。

    契约：返回可执行的 `Graph`；不接受参数，角色与目标由输入消息生成。
    副作用：构图阶段无 `I/O`；运行阶段会调用搜索与财经工具。
    失败语义：外部工具或模型不可用会在执行期失败；构图不抛错。
    关键路径（三步）：
    1) 生成 `Role` / `Goal` / `Backstory` 提示词
    2) 组装动态 `Agent` 与管理 `Agent`
    3) 用层级任务驱动最终输出
    异常流：工具调用失败会导致任务输出缺失或中断。
    性能瓶颈：外部检索与模型推理为主要耗时。
    排障入口：确认搜索工具与模型配置是否可用。
    决策：将管理 `Agent` 与执行 `Agent` 使用不同模型
    问题：需要兼顾调度能力与成本控制
    方案：`manager_llm` 使用更强模型，执行 Agent 使用轻量模型
    代价：模型配置更复杂，成本与延迟可变
    重评：当任务复杂度下降或成本压力上升时统一模型规格
    """
    llm = OpenAIModelComponent(model_name="gpt-4o-mini")
    manager_llm = OpenAIModelComponent(model_name="gpt-4o")
    search_api_tool = SearchAPIComponent()
    yahoo_search_tool = YfinanceToolComponent()
    dynamic_agent = CrewAIAgentComponent()
    chat_input = ChatInput()
    role_prompt = PromptComponent(_display_name="Role Prompt")
    role_prompt.set(
        template="""Define a Role that could execute or answer well the user's query.

User's query: {query}

Role should be two words max. Something like "Researcher" or "Software Developer".
"""
    )

    goal_prompt = PromptComponent(_display_name="Goal Prompt")
    goal_prompt.set(
        template="""Define the Goal of this Role, given the User's Query.
User's query: {query}

Role: {role}

The goal should be concise and specific.
Goal:
""",
        query=chat_input.message_response,
        role=role_prompt.build_prompt,
    )
    backstory_prompt = PromptComponent(_display_name="Backstory Prompt")
    backstory_prompt.set(
        template="""Define a Backstory of this Role and Goal, given the User's Query.
User's query: {query}

Role: {role}
Goal: {goal}

The backstory should be specific and well aligned with the rest of the information.
Backstory:""",
        query=chat_input.message_response,
        role=role_prompt.build_prompt,
        goal=goal_prompt.build_prompt,
    )
    dynamic_agent.set(
        tools=[search_api_tool.build_tool, yahoo_search_tool.build_tool],
        llm=llm.build_model,
        role=role_prompt.build_prompt,
        goal=goal_prompt.build_prompt,
        backstory=backstory_prompt.build_prompt,
    )

    response_prompt = PromptComponent()
    response_prompt.set(
        template="""User's query:
{query}

Respond to the user with as much as information as you can about the topic. Delete if needed.
If it is just a general query (e.g a greeting) you can respond them directly.""",
        query=chat_input.message_response,
    )
    manager_agent = CrewAIAgentComponent()
    manager_agent.set(
        llm=manager_llm.build_model,
        role="Manager",
        goal="You can answer general questions from the User and may call others for help if needed.",
        backstory="You are polite and helpful. You've always been a beacon of politeness.",
    )
    task = HierarchicalTaskComponent()
    task.set(
        task_description=response_prompt.build_prompt,
        expected_output="Succinct response that answers the User's query.",
    )
    crew_component = HierarchicalCrewComponent()
    crew_component.set(
        tasks=task.build_task, agents=[dynamic_agent.build_output], manager_agent=manager_agent.build_output
    )
    chat_output = ChatOutput()
    chat_output.set(input_value=crew_component.build_output)

    return Graph(
        start=chat_input,
        end=chat_output,
        flow_name="Sequential Tasks Agent",
        description="This Agent runs tasks in a predefined sequence.",
    )
