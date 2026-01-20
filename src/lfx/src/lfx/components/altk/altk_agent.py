"""
模块名称：ALTK 代理组件

本模块定义 ALTK 代理组件，组合工具前验证与工具后处理两类能力。
主要功能包括：
- 工具执行前进行 SPARC 校验与规格更新
- 工具输出后进行 JSON 解析与反思处理
- 统一输入消息格式，兼容不同 `to_lc_message` 实现

关键组件：
- ALTKAgentComponent：组合式代理实现
- PreToolValidationWrapper / PostToolProcessingWrapper：工具处理包装器

设计背景：在不改变基础 Agent 接口的前提下，增加可插拔的工具处理流水线。
注意事项：工具规格必须在流水线处理前更新，否则校验可能基于过期签名。
"""

from lfx.base.agents.altk_base_agent import ALTKBaseAgentComponent
from lfx.base.agents.altk_tool_wrappers import (
    PostToolProcessingWrapper,
    PreToolValidationWrapper,
)
from lfx.base.models.model_input_constants import MODEL_PROVIDERS_DICT, MODELS_METADATA
from lfx.components.models_and_agents.memory import MemoryComponent
from lfx.inputs.inputs import BoolInput
from lfx.io import DropdownInput, IntInput, Output
from lfx.log.logger import logger


def set_advanced_true(component_input):
    """标记输入为高级选项。

    输入：组件输入对象，需具备 `advanced` 属性。
    输出：原对象（原地修改）。
    副作用：会修改 `component_input.advanced`。
    """
    component_input.advanced = True
    return component_input


MODEL_PROVIDERS_LIST = ["Anthropic", "OpenAI"]
INPUT_NAMES_TO_BE_OVERRIDDEN = ["agent_llm"]


def get_parent_agent_inputs():
    """获取父类中未被覆盖的输入定义。

    输入：无（读取 `ALTKBaseAgentComponent.inputs`）。
    输出：过滤后的输入列表。
    失败语义：无显式失败，错误由输入列表结构不符合预期触发。
    """
    return [
        input_field
        for input_field in ALTKBaseAgentComponent.inputs
        if input_field.name not in INPUT_NAMES_TO_BE_OVERRIDDEN
    ]


class ALTKAgentComponent(ALTKBaseAgentComponent):
    """同时具备工具前验证与工具后处理能力的 ALTK 代理。

    契约：依赖 `ALTKBaseAgentComponent` 的输入/输出约定，并要求工具包装器可用。
    关键路径：初始化输入 -> 构建工具流水线 -> 更新工具规格并执行代理调用。
    决策：采用可插拔包装器流水线以复用基类能力并便于扩展。
    问题：需要在不改动基础接口的前提下叠加多种工具处理能力。
    方案：通过 `pipeline_manager` 组合包装器并按配置启停。
    代价：包装器顺序与状态需要额外维护。
    重评：当包装器数量显著增多或顺序依赖复杂化时评估专用编排层。
    失败语义：包装器或下游模型抛错时原样透传，调用方需处理运行时异常。
    """

    display_name: str = "ALTK Agent"
    description: str = "Advanced agent with both pre-tool validation and post-tool processing capabilities."
    documentation: str = "https://docs.langflow.org/bundles-altk"
    icon = "zap"
    beta = True
    name = "ALTK Agent"

    memory_inputs = [set_advanced_true(component_input) for component_input in MemoryComponent().inputs]

    # 决策：过滤 OpenAI 输入中的 `json_mode`
    # 问题：ALTK 组件自行处理结构化输出，直接暴露 `json_mode` 会造成配置冲突
    # 方案：在 `MODEL_PROVIDERS_DICT["OpenAI"]["inputs"]` 中剔除该项
    # 代价：该组件中无法直接开启 `json_mode`
    # 重评：当 ALTK 与 `json_mode` 语义兼容时恢复
    if "OpenAI" in MODEL_PROVIDERS_DICT:
        openai_inputs_filtered = [
            input_field
            for input_field in MODEL_PROVIDERS_DICT["OpenAI"]["inputs"]
            if not (hasattr(input_field, "name") and input_field.name == "json_mode")
        ]
    else:
        openai_inputs_filtered = []

    inputs = [
        DropdownInput(
            name="agent_llm",
            display_name="Model Provider",
            info="The provider of the language model that the agent will use to generate responses.",
            options=[*MODEL_PROVIDERS_LIST],
            value="OpenAI",
            real_time_refresh=True,
            refresh_button=False,
            input_types=[],
            options_metadata=[MODELS_METADATA[key] for key in MODEL_PROVIDERS_LIST if key in MODELS_METADATA],
        ),
        *get_parent_agent_inputs(),
        BoolInput(
            name="enable_tool_validation",
            display_name="Tool Validation",
            info="Validates tool calls using SPARC before execution.",
            value=True,
        ),
        BoolInput(
            name="enable_post_tool_reflection",
            display_name="Post Tool JSON Processing",
            info="Processes tool output through JSON analysis.",
            value=True,
        ),
        IntInput(
            name="response_processing_size_threshold",
            display_name="Response Processing Size Threshold",
            value=100,
            info="Tool output is post-processed only if response exceeds this character threshold.",
            advanced=True,
        ),
    ]
    outputs = [
        Output(name="response", display_name="Response", method="message_response"),
    ]

    def configure_tool_pipeline(self) -> None:
        """根据配置构建工具处理流水线。

        关键路径（三步）：
        1) 创建包装器列表并按策略追加。
        2) 根据开关决定是否启用前验证/后处理。
        3) 交由 `pipeline_manager` 完成注册。

        异常流：包装器初始化失败时抛出对应异常。
        排障入口：查看 `logger` 中 `Enabling ... Wrapper!` 日志。
        """
        wrappers = []

        # 决策：后处理为内层、前验证为外层
        # 问题：需要先处理工具输出再进行外层校验拦截
        # 方案：先追加后处理包装器，再追加前验证包装器以形成外包内结构
        # 代价：包装器顺序错误会改变行为
        # 重评：当 `pipeline_manager` 支持显式顺序参数时调整
        if self.enable_post_tool_reflection:
            logger.info("Enabling Post-Tool Processing Wrapper!")
            post_processor = PostToolProcessingWrapper(
                response_processing_size_threshold=self.response_processing_size_threshold
            )
            wrappers.append(post_processor)

        if self.enable_tool_validation:
            logger.info("Enabling Pre-Tool Validation Wrapper!")
            pre_validator = PreToolValidationWrapper()
            wrappers.append(pre_validator)

        self.pipeline_manager.configure_wrappers(wrappers)

    def update_runnable_instance(self, agent, runnable, tools):
        """在运行前更新工具规格并注入流水线处理后的工具集合。

        关键路径（三步）：
        1) 构建上下文并初始化工具流水线。
        2) 为校验包装器补充工具规格。
        3) 处理工具并写回 `runnable.tools`。

        异常流：包装器或工具处理失败时原样抛出。
        排障入口：关注校验失败的异常信息与工具规格转换逻辑。
        """
        user_query = self.get_user_query()
        conversation_context = self.build_conversation_context()

        self._initialize_tool_pipeline()

        # 注意：校验包装器依赖更新后的 `tool_specs`，否则会基于过期签名
        for wrapper in self.pipeline_manager.wrappers:
            if isinstance(wrapper, PreToolValidationWrapper) and tools:
                wrapper.tool_specs = wrapper.convert_langchain_tools_to_sparc_tool_specs_format(tools)

        processed_tools = self.pipeline_manager.process_tools(
            list(tools or []),
            agent=agent,
            user_query=user_query,
            conversation_context=conversation_context,
        )

        runnable.tools = processed_tools
        return runnable

    def __init__(self, **kwargs):
        """初始化 ALTK 代理并修复输入消息格式差异。

        输入：`**kwargs` 透传给父类。
        输出：无。
        副作用：可能替换 `self.input_value` 为规范化代理。
        """
        super().__init__(**kwargs)

        # 决策：对 `to_lc_message` 返回内容做规范化
        # 问题：部分输入实现会返回 `content` 为列表，导致下游模型处理不一致
        # 方案：使用代理在调用时统一转为字符串内容
        # 代价：可能掩盖上游格式错误
        # 重评：当所有输入源统一返回字符串内容时移除
        if hasattr(self.input_value, "to_lc_message") and callable(self.input_value.to_lc_message):
            self.input_value = self._create_normalized_input_proxy(self.input_value)

    def _create_normalized_input_proxy(self, original_input):
        """创建输入代理以统一 `to_lc_message` 的内容格式。

        输入：原始输入对象。
        输出：带规范化行为的代理对象。
        失败语义：若原始对象缺少必要方法，异常原样透传。
        """

        class NormalizedInputProxy:
            """输入代理，用于按需规范化消息内容。"""

            def __init__(self, original):
                self._original = original

            def __getattr__(self, name):
                if name == "to_lc_message":
                    return self._normalized_to_lc_message
                return getattr(self._original, name)

            def _normalized_to_lc_message(self):
                """返回规范化为字符串内容的消息对象。

                输入：无（调用原始 `to_lc_message`）。
                输出：`HumanMessage` 或 `AIMessage`，`content` 为字符串。
                失败语义：原始实现抛错时原样透传。
                """
                original_msg = self._original.to_lc_message()

                if hasattr(original_msg, "content") and isinstance(original_msg.content, list):
                    from langchain_core.messages import AIMessage, HumanMessage

                    from lfx.base.agents.altk_base_agent import (
                        normalize_message_content,
                    )

                    normalized_content = normalize_message_content(original_msg)

                    if isinstance(original_msg, HumanMessage):
                        return HumanMessage(content=normalized_content)
                    return AIMessage(content=normalized_content)

                return original_msg

            def __str__(self):
                return str(self._original)

            def __repr__(self):
                return f"NormalizedInputProxy({self._original!r})"

        return NormalizedInputProxy(original_input)
