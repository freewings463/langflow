"""
模块名称：顺序任务 `Agent` 示例图

本模块构建“研究 → 编辑 → 写作”顺序任务示例，用于展示多角色串行协作。主要功能包括：
- 使用 `SequentialTaskAgentComponent` 串联多任务
- 通过 `SequentialCrewComponent` 汇总顺序执行结果

关键组件：
- `sequential_tasks_agent_graph`: 构建顺序任务 `Graph`

设计背景：让新用户直观看到任务串行编排的最小范式。
注意事项：搜索工具与模型配置缺失会导致运行期失败。
"""

from lfx.components.crewai.sequential_crew import SequentialCrewComponent
from lfx.components.crewai.sequential_task_agent import SequentialTaskAgentComponent
from lfx.components.input_output import ChatOutput, TextInputComponent
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.components.tools import SearchAPIComponent
from lfx.graph import Graph


def sequential_tasks_agent_graph():
    """构建顺序任务链路的示例图。

    契约：返回 `Graph` 实例；示例默认主题为 `Agile`。
    副作用：构图阶段无 `I/O`；运行时调用搜索工具与模型。
    失败语义：搜索或模型不可用会在执行期失败。
    关键路径（三步）：
    1) 研究者检索并生成要点
    2) 编辑者修订并校正内容
    3) 喜剧写作者输出最终博客
    异常流：上一任务失败会导致后续任务缺少输入。
    性能瓶颈：外部检索与多轮模型调用。
    排障入口：确认搜索工具配置、模型权限与调用配额。
    决策：默认主题固定为 `Agile`
    问题：示例需要稳定且通用的输入
    方案：使用常见管理话题作为默认值
    代价：默认主题可能与用户场景不相关
    重评：当示例改为交互式输入时移除默认值
    """
    llm = OpenAIModelComponent()
    search_api_tool = SearchAPIComponent()

    text_input = TextInputComponent(_display_name="Topic")
    text_input.set(input_value="Agile")

    document_prompt_component = PromptComponent()
    document_prompt_component.set(
        template="""Topic: {topic}

Build a document about this topic.""",
        topic=text_input.text_response,
    )

    # 实现：研究者产出初稿，作为后续修订输入。
    researcher_task_agent = SequentialTaskAgentComponent()
    researcher_task_agent.set(
        role="Researcher",
        goal="Search Google to find information to complete the task.",
        backstory="Research has always been your thing. You can quickly find things on the web because of your skills.",
        tools=[search_api_tool.build_tool],
        llm=llm.build_model,
        task_description=document_prompt_component.build_prompt,
        expected_output="Bullet points and small phrases about the research topic.",
    )

    revision_prompt_component = PromptComponent()
    revision_prompt_component.set(
        template="""Topic: {topic}

Revise this document.""",
        topic=text_input.text_response,
    )

    # 实现：编辑者基于研究结果做校订。
    editor_task_agent = SequentialTaskAgentComponent()
    editor_task_agent.set(
        role="Editor",
        goal="You should edit the information provided by the Researcher to make it more palatable and to not contain "
        "misleading information.",
        backstory="You are the editor of the most reputable journal in the world.",
        llm=llm.build_model,
        task_description=revision_prompt_component.build_prompt,
        expected_output="Small paragraphs and bullet points with the corrected content.",
        previous_task=researcher_task_agent.build_agent_and_task,
    )

    blog_prompt_component = PromptComponent()
    blog_prompt_component.set(
        template="""Topic: {topic}

Build a fun blog post about this topic.""",
        topic=text_input.text_response,
    )

    # 实现：写作角色基于修订结果生成最终输出。
    comedian_task_agent = SequentialTaskAgentComponent()
    comedian_task_agent.set(
        role="Comedian",
        goal="You write comedic content based on the information provided by the editor.",
        backstory="Your formal occupation is Comedian-in-Chief. "
        "You write jokes, do standup comedy, and write funny articles.",
        llm=llm.build_model,
        task_description=blog_prompt_component.build_prompt,
        expected_output="A small blog about the topic.",
        previous_task=editor_task_agent.build_agent_and_task,
    )

    crew_component = SequentialCrewComponent()
    crew_component.set(
        tasks=comedian_task_agent.build_agent_and_task,
    )

    chat_output = ChatOutput()
    chat_output.set(input_value=crew_component.build_output)

    return Graph(
        start=text_input,
        end=chat_output,
        flow_name="Sequential Tasks Agent",
        description="This Agent runs tasks in a predefined sequence.",
    )
