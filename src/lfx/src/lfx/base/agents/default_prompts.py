"""
模块名称：代理默认提示模板

本模块集中存放代理默认提示模板，主要用于为特定代理类型提供统一的提示格式与工具调用约定。
主要功能包括：
- `XML` 代理默认提示模板

关键组件：
- `XML_AGENT_PROMPT`：`XML` 工具调用提示模板

设计背景：不同代理类型需要固定的提示结构以保证工具调用格式一致。
注意事项：模板内容为用户可见文本，调整需同步评估兼容性与提示长度。
"""

# 决策：`XML` 代理提示模板
# 问题：为 `XML` 格式的代理提供标准提示
# 方案：定义一个包含工具使用说明的模板
# 代价：硬编码的提示，不易于国际化
# 重评：当需要多语言支持或更灵活的提示配置时重新评估
XML_AGENT_PROMPT = """You are a helpful assistant. Help the user answer any questions.

            You have access to the following tools:

            {tools}

            In order to use a tool, you can use <tool></tool> and <tool_input></tool_input> tags. You will then get back a response in the form <observation></observation>
            For example, if you have a tool called 'search' that could run a google search, in order to search for the weather in SF you would respond:

            <tool>search</tool><tool_input>weather in SF</tool_input>
            <observation>64 degrees</observation>

            When you are done, respond with a final answer between <final_answer></final_answer>. For example:

            <final_answer>The weather in SF is 64 degrees</final_answer>

            Begin!

            Previous Conversation:
            {chat_history}

            Question: {input}
            {agent_scratchpad}"""  # noqa: E501
