"""模块名称：LLM 自校验链组件

本模块封装 LangChain `LLMCheckerChain`，用于在问答场景中进行自我核对。
主要功能包括：构建链、执行推理、返回标准 `Message`。

关键组件：
- `LLMCheckerChainComponent`：自校验链的组件化入口

设计背景：为高风险回答提供二次验证路径。
注意事项：依赖 `LLMCheckerChain` 行为，返回结果类型为 dict。
"""

from langchain.chains import LLMCheckerChain

from lfx.base.chains.model import LCChainComponent
from lfx.inputs.inputs import HandleInput, MultilineInput
from lfx.schema import Message


class LLMCheckerChainComponent(LCChainComponent):
    """LLM 自校验链组件。

    契约：输入 `input_value/llm`；输出 `Message`；副作用：更新 `self.status`；
    失败语义：链执行异常向上抛出。
    关键路径：1) 从 LLM 构建链 2) 执行 `invoke` 3) 提取 `output_key`。
    决策：使用 LangChain 默认输入/输出键
    问题：链内部键名可能随版本变化
    方案：读取 `chain.input_key/output_key`
    代价：对链对象有额外依赖
    重评：当 API 固化后可直接使用常量键
    """
    display_name = "LLMCheckerChain"
    description = "Chain for question-answering with self-verification."
    documentation = "https://python.langchain.com/docs/modules/chains/additional/llm_checker"
    name = "LLMCheckerChain"
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
    ]

    def invoke_chain(self) -> Message:
        """执行自校验链并返回消息。

        契约：输入 `input_value`；输出 `Message`；副作用：更新 `self.status`；
        失败语义：链执行异常透传。
        关键路径：1) 构建链 2) 执行调用 3) 规范化输出。
        决策：结果统一转为字符串
        问题：上游可能需要稳定的文本输出
        方案：`str(result)` 转换
        代价：丢失结构化信息
        重评：当需要结构化输出时返回原始 dict
        """
        chain = LLMCheckerChain.from_llm(llm=self.llm)
        response = chain.invoke(
            {chain.input_key: self.input_value},
            config={"callbacks": self.get_langchain_callbacks()},
        )
        result = response.get(chain.output_key, "")
        result = str(result)
        self.status = result
        return Message(text=result)
