"""
模块名称：文档问答示例图

本模块构建基于本地文件的问答示例图，用于演示“上传文档 → 构建上下文 → 回答问题”。主要功能包括：
- 读取文件内容作为上下文
- 生成问答提示并调用模型生成答案

关键组件：
- `document_qa_graph`: 构建文档问答 `Graph`

设计背景：新用户常见需求是对文档进行问答或摘要。
注意事项：运行时需要提供文件输入并配置模型服务。
"""

from lfx.components.data import FileComponent
from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models import LanguageModelComponent
from lfx.components.models_and_agents import PromptComponent
from lfx.graph import Graph


def document_qa_graph(template: str | None = None):
    """构建基于文件上下文的问答示例图。

    契约：`template=None` 使用默认问答模板；返回 `Graph` 实例。
    副作用：仅构图；运行时读取文件并调用模型。
    失败语义：文件读取或模型调用失败会在执行期抛错。
    关键路径：1) 读取文件内容 2) 组装问答提示 3) 生成回答。
    决策：将文件内容作为 `system_message` 提供给模型
    问题：需要显式控制上下文与用户问题的边界
    方案：使用提示模板将上下文与问题隔离
    代价：上下文过长时可能触发模型输入限制
    重评：当文档规模增大时引入分段检索或向量检索
    """
    if template is None:
        template = """Answer user's questions based on the document below:

---

{Document}

---

Question:
{Question}

Answer:
"""
    file_component = FileComponent()

    chat_input = ChatInput()
    prompt_component = PromptComponent()
    prompt_component.set(
        template=template,
        context=file_component.load_files_message,
        question=chat_input.message_response,
    )

    openai_component = LanguageModelComponent()
    openai_component.set(input_value=chat_input.message_response, system_message=prompt_component.build_prompt)

    chat_output = ChatOutput()
    chat_output.set(input_value=openai_component.text_response)

    return Graph(start=chat_input, end=chat_output)
