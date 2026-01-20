"""
模块名称：博客写作示例图

本模块构建“参考资料 → 指令 → 生成博客”的示例图，用于演示抓取网页内容并引导模型写作。主要功能包括：
- 通过 `URLComponent` 拉取参考页面并解析文本
- 使用指令输入与参考内容拼接生成写作提示

关键组件：
- `blog_writer_graph`: 构建基于参考资料的写作 `Graph`

设计背景：展示 `Langflow` 在“资料检索 + 写作”组合上的最小范式。
注意事项：示例包含外部 `URL`，运行时需要网络与模型配置。
"""

from textwrap import dedent

from lfx.components.data import URLComponent
from lfx.components.input_output import ChatOutput, TextInputComponent
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.components.processing import ParserComponent
from lfx.graph import Graph


def blog_writer_graph(template: str | None = None):
    """构建参考资料驱动的博客写作示例图。

    契约：`template=None` 使用默认写作模板；返回 `Graph` 实例。
    副作用：仅构图；执行阶段会访问外部 `URL` 并调用模型。
    失败语义：网络不可用或解析失败会在运行时抛错；构图阶段不触发。
    关键路径：1) 拉取与解析参考资料 2) 组合指令与模板 3) 生成响应。
    决策：参考资料固定为 `langflow.org` 与 `docs.langflow.org`
    问题：需要稳定且与产品相关的示例素材
    方案：选择官方站点作为示例来源
    代价：示例依赖外部站点可用性与内容变更
    重评：当站点结构变化或需要本地示例时替换来源
    """
    if template is None:
        template = dedent("""Reference 1:

{references}

---

{instructions}

Blog:
""")
    url_component = URLComponent()
    url_component.set(urls=["https://langflow.org/", "https://docs.langflow.org/"])
    parse_data_component = ParserComponent()
    parse_data_component.set(input_data=url_component.fetch_content)

    text_input = TextInputComponent(_display_name="Instructions")
    text_input.set(
        input_value="Use the references above for style to write a new blog/tutorial about Langflow and AI. "
        "Suggest non-covered topics."
    )

    prompt_component = PromptComponent()
    prompt_component.set(
        template=template,
        instructions=text_input.text_response,
        references=parse_data_component.parse_combined_text,
    )

    openai_component = OpenAIModelComponent()
    openai_component.set(input_value=prompt_component.build_prompt)

    chat_output = ChatOutput()
    chat_output.set(input_value=openai_component.text_response)

    return Graph(start=text_input, end=chat_output)
