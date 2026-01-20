"""
模块名称：Google Generative AI Chat 适配修复

本模块提供对 `ChatGoogleGenerativeAI` 的轻量修复子类，主要用于修正
Gemini 工具/函数消息缺失名称导致的请求构建问题。
主要功能包括：
- 在请求前补齐 `ToolMessage` / `FunctionMessage` 的名称
- 复用原始 LangChain 实现并保持接口一致

关键组件：
- `ChatGoogleGenerativeAIFixed`：修复版 Chat 实现

设计背景：部分模型在工具响应为空名时会拒绝请求，需在客户端侧兜底。
注意事项：仅修补消息名称，不改变消息内容与顺序。
"""

from langchain_google_genai import ChatGoogleGenerativeAI


class ChatGoogleGenerativeAIFixed(ChatGoogleGenerativeAI):
    """Gemini 请求构建修复版 Chat 类。

    契约：输入消息列表与父类一致；输出仍由父类 `_prepare_request` 负责构建。
    副作用：在请求前可能替换消息实例以补齐名称字段。
    失败语义：依赖包缺失时抛 `ImportError`。
    决策：在客户端侧补齐空名称
    问题：空 `name` 的工具/函数消息会导致 Gemini 请求失败
    方案：在 `_prepare_request` 统一补齐默认名称
    代价：新增的名称为默认值，可能与上游真实名称不一致
    重评：当上游修复或协议允许空名时可移除此补丁
    """

    def __init__(self, *args, **kwargs):
        """初始化修复版模型实例。

        契约：与父类构造参数一致。
        失败语义：缺少 `langchain_google_genai` 依赖时抛 `ImportError`。
        """
        if ChatGoogleGenerativeAI is None:
            msg = "The 'langchain_google_genai' package is required to use the Google Generative AI model."
            raise ImportError(msg)

        # 注意：保持与父类初始化逻辑一致
        super().__init__(*args, **kwargs)

    def _prepare_request(self, messages, **kwargs):
        """构建请求前修补空名称的工具/函数消息。

        关键路径（三步）：
        1) 遍历消息并识别 `ToolMessage`/`FunctionMessage`
        2) 为缺失 `name` 的消息补齐默认名称
        3) 将修补后的消息交给父类处理
        异常流：消息类型异常将由父类处理或抛出。
        性能瓶颈：线性遍历消息列表。
        排障入口：关注空 `name` 触发的下游报错是否消失。
        """
        from langchain_core.messages import FunctionMessage, ToolMessage

        # 预处理：确保工具/函数消息具备名称
        fixed_messages = []
        for message in messages:
            fixed_message = message
            if isinstance(message, ToolMessage) and not message.name:
                # 补齐工具消息默认名称
                fixed_message = ToolMessage(
                    content=message.content,
                    name="tool_response",
                    tool_call_id=getattr(message, "tool_call_id", None),
                    artifact=getattr(message, "artifact", None),
                )
            elif isinstance(message, FunctionMessage) and not message.name:
                # 补齐函数消息默认名称
                fixed_message = FunctionMessage(content=message.content, name="function_response")
            fixed_messages.append(fixed_message)

        # 使用修补后的消息调用父类实现
        return super()._prepare_request(fixed_messages, **kwargs)
