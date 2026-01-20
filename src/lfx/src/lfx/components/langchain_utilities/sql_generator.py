"""模块名称：自然语言转 SQL 组件

本模块封装 LangChain `create_sql_query_chain`，将自然语言问题转为 SQL 查询语句。
主要功能包括：校验 `top_k` 与提示词、构建查询链、输出 SQL 文本。

关键组件：
- `SQLGeneratorComponent`：NL2SQL 组件入口

设计背景：在数据查询场景提供可追踪的 SQL 生成能力。
注意事项：自定义 `prompt` 必须包含 `{question}`。
"""

from typing import TYPE_CHECKING

from langchain.chains import create_sql_query_chain
from langchain_core.prompts import PromptTemplate

from lfx.base.chains.model import LCChainComponent
from lfx.inputs.inputs import HandleInput, IntInput, MultilineInput
from lfx.schema import Message
from lfx.template.field.base import Output

if TYPE_CHECKING:
    from langchain_core.runnables import Runnable


class SQLGeneratorComponent(LCChainComponent):
    """自然语言转 SQL 组件。

    契约：输入 `input_value/llm/db/top_k/prompt`；输出 `Message`（SQL 字符串）；
    副作用：更新 `self.status`；失败语义：`top_k` 非正数或提示词缺少占位符时抛 `ValueError`。
    关键路径：1) 校验参数 2) 构建查询链 3) 执行并清洗 SQL。
    决策：对输出做 `SQLQuery:` 前缀清理
    问题：链输出包含固定前缀影响下游
    方案：在 runnable 中清理前缀
    代价：依赖输出格式稳定
    重评：当上游输出格式改变时调整清理逻辑
    """
    display_name = "Natural Language to SQL"
    description = "Generate SQL from natural language."
    name = "SQLGenerator"
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
            name="db",
            display_name="SQLDatabase",
            input_types=["SQLDatabase"],
            required=True,
        ),
        IntInput(
            name="top_k",
            display_name="Top K",
            info="The number of results per select statement to return.",
            value=5,
        ),
        MultilineInput(
            name="prompt",
            display_name="Prompt",
            info="The prompt must contain `{question}`.",
        ),
    ]

    outputs = [Output(display_name="Message", name="text", method="invoke_chain")]

    def invoke_chain(self) -> Message:
        """生成 SQL 并返回文本。

        关键路径（三步）：
        1) 构建或使用自定义提示模板
        2) 校验 `top_k` 与 `{question}` 占位符
        3) 执行查询链并清理输出

        异常流：参数不合法抛 `ValueError`；执行异常透传。
        排障入口：`self.status` 保存最终 SQL。
        决策：当 `prompt` 为空时使用默认模板
        问题：降低配置门槛
        方案：`prompt` 为空即走默认
        代价：默认提示不适配领域特定 SQL 风格
        重评：当有领域模板时改为强制提示
        """
        prompt_template = PromptTemplate.from_template(template=self.prompt) if self.prompt else None

        if self.top_k < 1:
            msg = "Top K must be greater than 0."
            raise ValueError(msg)

        if not prompt_template:
            sql_query_chain = create_sql_query_chain(llm=self.llm, db=self.db, k=self.top_k)
        else:
            # 校验提示词必须包含 `{question}`
            if "{question}" not in prompt_template.template or "question" not in prompt_template.input_variables:
                msg = "Prompt must contain `{question}` to be used with Natural Language to SQL."
                raise ValueError(msg)
            sql_query_chain = create_sql_query_chain(llm=self.llm, db=self.db, prompt=prompt_template, k=self.top_k)
        query_writer: Runnable = sql_query_chain | {"query": lambda x: x.replace("SQLQuery:", "").strip()}
        response = query_writer.invoke(
            {"question": self.input_value},
            config={"callbacks": self.get_langchain_callbacks()},
        )
        query = response.get("query")
        self.status = query
        return query
