"""模块名称：对话记忆链组件

本模块封装 LangChain 的 `ConversationChain`，用于在对话场景中自动加载/更新记忆。
主要功能包括：选择是否注入 `memory`、执行链并输出 `Message`。

关键组件：
- `ConversationChainComponent`：对话链的组件化入口

设计背景：在 Langflow 中统一对话链与回调管理方式。
注意事项：未安装 `langchain` 会抛 `ImportError`，返回值可能是 dict 或 str。
"""

from lfx.base.chains.model import LCChainComponent
from lfx.inputs.inputs import HandleInput, MultilineInput
from lfx.schema.message import Message


class ConversationChainComponent(LCChainComponent):
    """对话链组件。

    契约：输入 `input_value/llm/memory`；输出 `Message`；副作用：更新 `self.status`；
    失败语义：缺少 `langchain` 时抛 `ImportError`，链返回非字符串时会强制转为 `str`。
    关键路径：1) 校验依赖 2) 构造链（含可选记忆）3) 调用并规范化输出。
    决策：无记忆时创建最小链
    问题：记忆为空不应阻塞对话
    方案：分支构造 `ConversationChain`
    代价：缺少对话上下文
    重评：当需要强制记忆一致性时改为必填
    """
    display_name = "ConversationChain"
    description = "Chain to have a conversation and load context from memory."
    name = "ConversationChain"
    legacy: bool = True
    icon = "LangChain"

    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Input",
            info="The input value to pass to the chain.",
            required=True,
        ),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
        ),
        HandleInput(
            name="memory",
            display_name="Memory",
            input_types=["BaseChatMemory"],
        ),
    ]

    def invoke_chain(self) -> Message:
        """执行对话链并标准化输出。

        关键路径（三步）：
        1) 处理依赖异常并实例化链
        2) 执行 `invoke` 获取结果
        3) 兼容 dict/str 返回并写入 `status`

        异常流：`langchain` 缺失抛 `ImportError`。
        排障入口：异常提示含安装命令 `uv pip install langchain`。
        """
        try:
            from langchain.chains import ConversationChain
        except ImportError as e:
            msg = (
                "ConversationChain requires langchain to be installed. Please install it with "
                "`uv pip install langchain`."
            )
            raise ImportError(msg) from e

        if not self.memory:
            chain = ConversationChain(llm=self.llm)
        else:
            chain = ConversationChain(llm=self.llm, memory=self.memory)

        result = chain.invoke(
            {"input": self.input_value},
            config={"callbacks": self.get_langchain_callbacks()},
        )
        if isinstance(result, dict):
            result = result.get(chain.output_key, "")

        elif not isinstance(result, str):
            result = result.get("response")
        result = str(result)
        self.status = result
        return Message(text=result)
