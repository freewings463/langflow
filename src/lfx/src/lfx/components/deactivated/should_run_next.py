"""
模块名称：可运行性判断组件（已停用）

本模块提供通过 LLM 判断是否继续执行下一个节点的能力，主要用于旧流程中的条件停止。主要功能包括：
- 构造提示词并调用 LLM 返回 yes/no
- 根据判断结果决定是否停止当前图执行

关键组件：
- `ShouldRunNextComponent`：可运行性判断组件

设计背景：早期流程中需要 LLM 参与控制流决策。
注意事项：依赖 LLM 输出严格为 `yes`/`no`，否则会继续重试。
"""

from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.field_typing import LanguageModel, Text


class ShouldRunNextComponent(CustomComponent):
    """可运行性判断组件。

    契约：返回原 `context`，并在内部决定是否 `stop()`。
    失败语义：LLM 调用失败由底层抛异常。
    副作用：可能调用 `self.stop()` 终止后续执行。
    """
    display_name = "Should Run Next"
    description = "Determines if a vertex is runnable."
    name = "ShouldRunNext"

    def build(self, llm: LanguageModel, question: str, context: str, retries: int = 3) -> Text:
        """判断是否需要继续运行后续节点。

        契约：最多重试 `retries` 次，期望 LLM 返回 `yes/no`。
        失败语义：多次未命中将使用最后一次结果推断；LLM 异常上抛。
        副作用：条件为否时调用 `self.stop()`。

        关键路径（三步）：
        1) 构造提示词并建立 chain
        2) 重试调用 LLM 获取 `yes/no`
        3) 根据结果决定是否停止
        """
        template = (
            "Given the following question and the context below, answer with a yes or no.\n\n"
            "{error_message}\n\n"
            "Question: {question}\n\n"  # noqa: RUF100, RUF027
            "Context: {context}\n\n"  # noqa: RUF100, RUF027
            "Answer:"
        )

        prompt = PromptTemplate.from_template(template)
        chain = prompt | llm
        error_message = ""
        for _i in range(retries):
            result = chain.invoke(
                {"question": question, "context": context, "error_message": error_message},
                config={"callbacks": self.get_langchain_callbacks()},
            )
            if isinstance(result, BaseMessage):
                content = result.content
            elif isinstance(result, str):
                content = result
            if isinstance(content, str) and content.lower().strip() in {"yes", "no"}:
                break
        condition = str(content).lower().strip() == "yes"
        self.status = f"Should Run Next: {condition}"
        if condition is False:
            self.stop()
        return context
