"""模块名称：LLM 数学链组件

本模块封装 LangChain `LLMMathChain`，用于把自然语言数学问题转为可执行计算。
主要功能包括：构建数学链、执行推理并返回消息。

关键组件：
- `LLMMathChainComponent`：数学链组件入口

设计背景：将数学推理任务交给 LLM+Python 执行。
注意事项：LLM 会生成代码片段，输入需控制安全范围。
"""

from langchain.chains import LLMMathChain

from lfx.base.chains.model import LCChainComponent
from lfx.inputs.inputs import HandleInput, MultilineInput
from lfx.schema import Message
from lfx.template.field.base import Output


class LLMMathChainComponent(LCChainComponent):
    """LLM 数学链组件。

    契约：输入 `input_value/llm`；输出 `Message`；副作用：更新 `self.status`；
    失败语义：链执行异常向上抛出。
    关键路径：1) 构建数学链 2) 执行 `invoke` 3) 提取输出并规范化。
    决策：统一用 `LLMMathChain.from_llm`
    问题：不同 LLM 需要一致的链构建方式
    方案：依赖 LangChain 工厂方法
    代价：对 LangChain 版本兼容性敏感
    重评：当链初始化参数需要暴露时扩展输入
    """
    display_name = "LLMMathChain"
    description = "Chain that interprets a prompt and executes python code to do math."
    documentation = "https://python.langchain.com/docs/modules/chains/additional/llm_math"
    name = "LLMMathChain"
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

    outputs = [Output(display_name="Message", name="text", method="invoke_chain")]

    def invoke_chain(self) -> Message:
        """执行数学链并返回消息。

        契约：输入 `input_value`；输出 `Message`；副作用：更新 `self.status`；
        失败语义：链执行异常透传。
        关键路径：1) 执行调用 2) 抽取输出键 3) 转为字符串。
        决策：将结果强制转为字符串
        问题：下游只接受文本节点
        方案：`str(result)` 统一输出类型
        代价：丢失结构化数值类型
        重评：当下游支持数值类型时返回原始值
        """
        chain = LLMMathChain.from_llm(llm=self.llm)
        response = chain.invoke(
            {chain.input_key: self.input_value},
            config={"callbacks": self.get_langchain_callbacks()},
        )
        result = response.get(chain.output_key, "")
        result = str(result)
        self.status = result
        return Message(text=result)
