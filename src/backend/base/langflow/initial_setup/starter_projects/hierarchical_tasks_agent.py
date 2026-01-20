"""
模块名称：层级任务 `Agent` 示例图

本模块构建“研究者 + 编辑者 + 管理者”的层级任务示例，用于展示多 `Agent` 复核与协作。主要功能包括：
- 研究者调用搜索工具获取信息
- 编辑者进行事实与偏差审阅
- 管理者汇总输出

关键组件：
- `hierarchical_tasks_agent_graph`: 构建层级协作 `Graph`

设计背景：展示多角色协作、检索与审阅的典型工作流。
注意事项：搜索工具与模型需配置；执行阶段可能产生外部调用成本。
"""

from lfx.components.crewai.crewai import CrewAIAgentComponent
from lfx.components.crewai.hierarchical_crew import HierarchicalCrewComponent
from lfx.components.crewai.hierarchical_task import HierarchicalTaskComponent
from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.components.tools import SearchAPIComponent
from lfx.graph import Graph


def hierarchical_tasks_agent_graph():
    """构建研究/编辑/管理三角色的层级任务示例图。

    契约：返回 `Graph` 实例；角色分工与提示词固定在示例中。
    副作用：构图阶段无 `I/O`；运行时调用搜索工具与模型。
    失败语义：搜索或模型不可用会在执行期失败。
    关键路径（三步）：
    1) 研究者检索并产出初始内容
    2) 编辑者审阅并修订内容
    3) 管理者整合并输出答案
    异常流：搜索结果为空会导致输出质量下降。
    性能瓶颈：外部检索与多轮模型调用。
    排障入口：确认搜索工具配置与模型连接是否可用。
    决策：研究者与编辑者使用同一模型
    问题：示例需降低成本并保持一致性
    方案：共享 `llm` 并由管理者单独配置模型
    代价：不同角色差异化能力受限
    重评：当需要更强编辑能力时为编辑者配置更强模型
    """
    llm = OpenAIModelComponent(model_name="gpt-4o-mini")
    manager_llm = OpenAIModelComponent(model_name="gpt-4o")
    search_api_tool = SearchAPIComponent()
    researcher_agent = CrewAIAgentComponent()
    chat_input = ChatInput()
    researcher_agent.set(
        tools=[search_api_tool.build_tool],
        llm=llm.build_model,
        role="Researcher",
        goal="Search for information about the User's query and answer as best as you can",
        backstory="You are a reliable researcher and journalist ",
    )

    editor_agent = CrewAIAgentComponent()

    editor_agent.set(
        llm=llm.build_model,
        role="Editor",
        goal="Evaluate the information for misleading or biased data.",
        backstory="You are a reliable researcher and journalist ",
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
        tasks=task.build_task,
        agents=[researcher_agent.build_output, editor_agent.build_output],
        manager_agent=manager_agent.build_output,
    )
    chat_output = ChatOutput()
    chat_output.set(input_value=crew_component.build_output)

    return Graph(
        start=chat_input,
        end=chat_output,
        flow_name="Sequential Tasks Agent",
        description="This Agent runs tasks in a predefined sequence.",
    )
