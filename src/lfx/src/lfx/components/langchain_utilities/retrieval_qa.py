"""模块名称：检索式问答链组件

本模块封装 LangChain `RetrievalQA`，用于结合检索器与 LLM 完成问答。
主要功能包括：构建链、执行检索问答、可选返回来源文档。

关键组件：
- `RetrievalQAComponent`：检索问答链组件入口

设计背景：在问答流程中统一检索与回答的组合方式。
注意事项：内部总是拉取 `source_documents` 以便排障。
"""

from typing import cast

from langchain.chains import RetrievalQA

from lfx.base.chains.model import LCChainComponent
from lfx.inputs.inputs import BoolInput, DropdownInput, HandleInput, MultilineInput
from lfx.schema import Message


class RetrievalQAComponent(LCChainComponent):
    """检索式问答链组件。

    契约：输入 `input_value/llm/retriever/memory/chain_type`；输出 `Message`；
    副作用：更新 `self.status`，可能修改 `memory` 键名；失败语义：链执行异常向上抛出。
    关键路径：1) 处理 `memory` 键 2) 构建 RetrievalQA 链 3) 执行并组装输出。
    决策：始终请求 `source_documents`
    问题：排障需要完整检索上下文
    方案：固定 `return_source_documents=True`
    代价：返回体更大，内存占用上升
    重评：当性能敏感且无需排障时允许关闭
    """
    display_name = "Retrieval QA"
    description = "Chain for question-answering querying sources from a retriever."
    name = "RetrievalQA"
    legacy: bool = True
    icon = "LangChain"
    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Input",
            info="The input value to pass to the chain.",
            required=True,
        ),
        DropdownInput(
            name="chain_type",
            display_name="Chain Type",
            info="Chain type to use.",
            options=["Stuff", "Map Reduce", "Refine", "Map Rerank"],
            value="Stuff",
            advanced=True,
        ),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
        ),
        HandleInput(
            name="retriever",
            display_name="Retriever",
            input_types=["Retriever"],
            required=True,
        ),
        HandleInput(
            name="memory",
            display_name="Memory",
            input_types=["BaseChatMemory"],
        ),
        BoolInput(
            name="return_source_documents",
            display_name="Return Source Documents",
            value=False,
        ),
    ]

    def invoke_chain(self) -> Message:
        """执行检索问答并返回结果。

        关键路径（三步）：
        1) 标准化链类型并设置记忆键
        2) 构建 `RetrievalQA` 并执行
        3) 组装最终输出与引用

        异常流：检索器/LLM 执行异常透传。
        排障入口：`self.status` 保存原始结果与 `source_documents`。
        决策：将 `memory.input_key/output_key` 固定为 `query/result`
        问题：RetrievalQA 需要特定键名与记忆对齐
        方案：在调用前同步记忆键名
        代价：可能影响共享 `memory` 的其他链
        重评：当记忆可独立注入时取消强制设置
        """
        chain_type = self.chain_type.lower().replace(" ", "_")
        if self.memory:
            self.memory.input_key = "query"
            self.memory.output_key = "result"

        runnable = RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type=chain_type,
            retriever=self.retriever,
            memory=self.memory,
            # 始终携带来源文档以便排障
            return_source_documents=True,
        )

        result = runnable.invoke(
            {"query": self.input_value},
            config={"callbacks": self.get_langchain_callbacks()},
        )

        source_docs = self.to_data(result.get("source_documents", keys=[]))
        result_str = str(result.get("result", ""))
        if self.return_source_documents and len(source_docs):
            references_str = self.create_references_from_data(source_docs)
            result_str = f"{result_str}\n{references_str}"
        # 将完整结果写入状态用于排障
        self.status = {**result, "source_documents": source_docs, "output": result_str}
        return cast("Message", result_str)
