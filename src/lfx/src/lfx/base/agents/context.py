"""
模块名称：代理上下文模型

本模块提供代理运行时上下文的数据结构与序列化逻辑，主要用于记录工具、`LLM`、
思考轨迹与上下文历史，便于回放与调试。
主要功能包括：
- 上下文序列化与数据视图生成
- LLM 类型校验与工具绑定
- 上下文历史维护与格式化输出

关键组件：
- `AgentContext`：上下文模型

设计背景：需要在代理执行过程中保留可追溯的上下文快照。
注意事项：`llm` 必须是 `LangChain` 语言模型类型，否则会抛出 `TypeError`。
"""

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseLanguageModel, BaseLLM
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, field_validator, model_serializer

from lfx.field_typing import LanguageModel
from lfx.schema.data import Data


class AgentContext(BaseModel):
    """代理上下文类，用于管理代理运行时的上下文信息

    关键路径（三步）：
    1) 初始化工具、LLM 和上下文信息
    2) 更新和跟踪上下文历史
    3) 序列化上下文以供使用

    异常流：LLM 验证失败时抛出 TypeError。
    性能瓶颈：大量上下文历史记录时序列化开销。
    排障入口：日志关键字 "validate_llm"。
    
    契约：
    - 输入：工具字典、LLM 实例和其他上下文参数
    - 输出：AgentContext 实例
    - 副作用：绑定工具到 LLM（如果支持）
    - 失败语义：如果 LLM 类型无效，抛出 TypeError
    """
    tools: dict[str, Any]
    llm: Any
    context: str = ""
    iteration: int = 0
    max_iterations: int = 5
    thought: str = ""
    last_action: Any = None
    last_action_result: Any = None
    final_answer: Any = ""
    context_history: list[tuple[str, str, str]] = Field(default_factory=list)

    @model_serializer(mode="plain")
    def serialize_agent_context(self):
        """序列化代理上下文

        契约：
        - 输入：无
        - 输出：包含序列化上下文信息的字典
        - 副作用：将 LLM 和工具转换为 JSON 格式
        - 失败语义：如果序列化失败，返回字符串表示
        """
        serliazed_llm = self.llm.to_json() if hasattr(self.llm, "to_json") else str(self.llm)
        serliazed_tools = {k: v.to_json() if hasattr(v, "to_json") else str(v) for k, v in self.tools.items()}
        return {
            "tools": serliazed_tools,
            "llm": serliazed_llm,
            "context": self.context,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "thought": self.thought,
            "last_action": self.last_action.to_json()
            if hasattr(self.last_action, "to_json")
            else str(self.last_action),
            "action_result": self.last_action_result.to_json()
            if hasattr(self.last_action_result, "to_json")
            else str(self.last_action_result),
            "final_answer": self.final_answer,
            "context_history": self.context_history,
        }

    @field_validator("llm", mode="before")
    @classmethod
    def validate_llm(cls, v) -> LanguageModel:
        """验证 LLM 是否为有效的语言模型

        契约：
        - 输入：待验证的语言模型对象
        - 输出：经过验证的语言模型
        - 副作用：无
        - 失败语义：如果 LLM 类型无效，抛出 TypeError
        """
        if not isinstance(v, BaseLLM | BaseChatModel | BaseLanguageModel):
            msg = "llm must be an instance of LanguageModel"
            raise TypeError(msg)
        return v

    def to_data_repr(self):
        """将代理上下文转换为数据表示

        契约：
        - 输入：无
        - 输出：Data 对象列表
        - 副作用：创建包含上下文历史的 Data 对象
        - 失败语义：如果转换失败，抛出相应异常
        """
        data_objs = []
        for name, val, time_str in self.context_history:
            content = val.content if hasattr(val, "content") else val
            data_objs.append(Data(name=name, value=content, timestamp=time_str))

        sorted_data_objs = sorted(data_objs, key=lambda x: datetime.fromisoformat(x.timestamp), reverse=True)

        sorted_data_objs.append(
            Data(
                name="Formatted Context",
                value=self.get_full_context(),
            )
        )
        return sorted_data_objs

    def _build_tools_context(self):
        """构建工具上下文字符串

        契约：
        - 输入：无
        - 输出：包含工具信息的字符串
        - 副作用：无
        - 失败语义：无
        """
        tool_context = ""
        for tool_name, tool_obj in self.tools.items():
            tool_context += f"{tool_name}: {tool_obj.description}\n"
        return tool_context

    def _build_init_context(self):
        """构建初始上下文

        契约：
        - 输入：无
        - 输出：格式化的上下文字符串
        - 副作用：无
        - 失败语义：无
        """
        return f"""
{self.context}

"""

    def model_post_init(self, _context: Any) -> None:
        """模型初始化后的处理

        契约：
        - 输入：初始化上下文
        - 输出：无
        - 副作用：绑定工具到 LLM 并更新上下文
        - 失败语义：如果绑定失败，抛出相应异常
        """
        if hasattr(self.llm, "bind_tools"):
            self.llm = self.llm.bind_tools(self.tools.values())
        if self.context:
            self.update_context("Initial Context", self.context)

    def update_context(self, key: str, value: str):
        """更新上下文历史

        契约：
        - 输入：键和值字符串
        - 输出：无
        - 副作用：在上下文历史列表开头插入新的元组
        - 失败语义：如果更新失败，抛出相应异常
        """
        self.context_history.insert(0, (key, value, datetime.now(tz=timezone.utc).astimezone().isoformat()))

    def _serialize_context_history_tuple(self, context_history_tuple: tuple[str, str, str]) -> str:
        """序列化上下文历史元组

        契约：
        - 输入：包含名称、值和时间戳的元组
        - 输出：格式化的字符串
        - 副作用：提取内容或日志属性
        - 失败语义：如果序列化失败，返回默认格式
        """
        name, value, _ = context_history_tuple
        if hasattr(value, "content"):
            value = value.content
        elif hasattr(value, "log"):
            value = value.log
        return f"{name}: {value}"

    def get_full_context(self) -> str:
        """获取完整上下文

        契约：
        - 输入：无
        - 输出：格式化的完整上下文字符串
        - 副作用：反转上下文历史以获得正确顺序
        - 失败语义：如果获取失败，返回空上下文
        """
        context_history_reversed = self.context_history[::-1]
        context_formatted = "\n".join(
            [
                self._serialize_context_history_tuple(context_history_tuple)
                for context_history_tuple in context_history_reversed
            ]
        )
        return f"""
Context:
{context_formatted}
"""
