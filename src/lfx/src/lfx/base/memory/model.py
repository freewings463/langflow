"""
模块名称：LangChain 记忆组件适配层

本模块提供将 LFX 组件体系与 LangChain 记忆实现对接的抽象基类，
主要用于构建 `BaseChatMemory` 并输出为组件可用的 `Memory` 对象。
主要功能包括：
- 统一输出验证，确保组件定义包含必需的输出方法
- 使用 LangChain `ConversationBufferMemory` 包装消息历史

关键组件：
- `LCChatMemoryComponent`：LangChain 记忆适配基类

设计背景：复用 LangChain 记忆机制，同时保持 LFX 组件输出接口一致。
注意事项：子类必须实现 `build_message_history`，否则无法构建 `memory`。
"""

from abc import abstractmethod

from langchain.memory import ConversationBufferMemory

from lfx.custom.custom_component.component import Component
from lfx.field_typing import BaseChatMemory
from lfx.field_typing.constants import Memory
from lfx.template.field.base import Output


class LCChatMemoryComponent(Component):
    """LangChain 记忆适配组件基类。

    契约：`build_message_history()` 返回 `Memory`；`build_base_memory()` 返回 `BaseChatMemory`。
    副作用：无直接 I/O，但子类构建历史时可能触发存储读取。
    失败语义：输出配置缺失会抛 `ValueError`；子类构建失败异常不在此处捕获。
    决策：使用 LangChain `ConversationBufferMemory` 封装消息历史
    问题：需要在 LFX 组件体系中复用 LangChain 记忆能力
    方案：基类统一输出验证并包装消息历史
    代价：强依赖 LangChain 记忆对象形态
    重评：当内部记忆协议与 LangChain 偏离时评估替换
    """

    trace_type = "chat_memory"
    outputs = [
        Output(
            display_name="Memory",
            name="memory",
            method="build_message_history",
        )
    ]

    def _validate_outputs(self) -> None:
        """校验输出声明是否包含必需的方法名。

        契约：`outputs` 中必须包含名为 `build_message_history` 的输出且类中存在同名方法。
        失败语义：缺失输出或方法时抛 `ValueError`，阻止组件在运行期不完整配置。
        决策：在基类中集中校验输出契约
        问题：输出配置错误会在运行时引入难排障问题
        方案：启动阶段即验证输出声明与方法实现
        代价：增加一次线性扫描 `outputs`
        重评：当输出系统提供编译期校验时可移除
        """

        required_output_methods = ["build_message_history"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    def build_base_memory(self) -> BaseChatMemory:
        """构建 LangChain 的基础记忆对象。

        契约：返回 `BaseChatMemory` 且其 `chat_memory` 来自 `build_message_history()`。
        失败语义：`build_message_history` 失败会直接抛出异常。
        决策：使用 `ConversationBufferMemory` 作为默认包装
        问题：需要最小化记忆实现差异以简化集成
        方案：统一包装消息历史为 `ConversationBufferMemory`
        代价：非缓冲型记忆需要额外适配
        重评：当出现多种记忆类型时考虑工厂化
        """

        return ConversationBufferMemory(chat_memory=self.build_message_history())

    @abstractmethod
    def build_message_history(self) -> Memory:
        """构建聊天消息历史对象。

        契约：返回 `Memory`，应可被 `ConversationBufferMemory` 接受。
        失败语义：初始化失败应抛出具体异常供上层记录。
        决策：由子类实现具体记忆来源
        问题：记忆后端与消息格式差异大
        方案：将历史构建抽象化
        代价：基类无法统一校验具体实现
        重评：当后端统一时考虑提供默认实现
        """
