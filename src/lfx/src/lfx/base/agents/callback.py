"""
模块名称：代理回调处理器

本模块提供 `LangChain` 异步回调处理器，用于将链路事件转发到统一日志函数，
以便前端展示与排障分析。
主要功能包括：
- 代理/工具/链路事件的标准化封装
- 将事件数据路由到日志回调

关键组件：
- `AgentAsyncHandler`：异步回调处理器

设计背景：需要统一事件格式以便 `UI` 与日志系统消费。
注意事项：`log_function` 为空时所有回调将被忽略。
"""

from typing import Any
from uuid import UUID

from langchain.callbacks.base import AsyncCallbackHandler
from langchain_core.agents import AgentAction, AgentFinish

from lfx.schema.log import LogFunctionType


class AgentAsyncHandler(AsyncCallbackHandler):
    """用于处理 LangChain 回调的异步回调处理器

    关键路径（三步）：
    1) 初始化日志函数
    2) 监听不同类型的回调事件
    3) 将事件数据传递给日志函数

    异常流：当日志函数为 None 时，不执行任何操作。
    性能瓶颈：无显著性能瓶颈。
    排障入口：日志关键字 "Chain Start"、"Tool Start"、"Agent Action" 等。
    
    契约：
    - 输入：日志函数
    - 输出：AgentAsyncHandler 实例
    - 副作用：存储日志函数供回调使用
    - 失败语义：如果日志函数为 None，则忽略所有回调
    """

    def __init__(self, log_function: LogFunctionType | None = None):
        """初始化异步回调处理器

        契约：
        - 输入：可选的日志函数
        - 输出：初始化的 AgentAsyncHandler 实例
        - 副作用：存储日志函数
        - 失败语义：无
        """
        self.log_function = log_function

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """链开始时的回调处理

        契约：
        - 输入：序列化数据、输入、运行ID等参数
        - 输出：无
        - 副作用：调用日志函数记录事件
        - 失败语义：如果日志函数为 None，则不执行任何操作
        """
        if self.log_function is None:
            return
        self.log_function(
            {
                "type": "chain_start",
                "serialized": serialized,
                "inputs": inputs,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "tags": tags,
                "metadata": metadata,
                **kwargs,
            },
            name="Chain Start",
        )

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """工具开始时的回调处理

        契约：
        - 输入：序列化数据、输入字符串、运行ID等参数
        - 输出：无
        - 副作用：调用日志函数记录事件
        - 失败语义：如果日志函数为 None，则不执行任何操作
        """
        if self.log_function is None:
            return
        self.log_function(
            {
                "type": "tool_start",
                "serialized": serialized,
                "input_str": input_str,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "tags": tags,
                "metadata": metadata,
                "inputs": inputs,
                **kwargs,
            },
            name="Tool Start",
        )

    async def on_tool_end(self, output: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        """工具结束时的回调处理

        契约：
        - 输入：工具输出、运行ID等参数
        - 输出：无
        - 副作用：调用日志函数记录事件
        - 失败语义：如果日志函数为 None，则不执行任何操作
        """
        if self.log_function is None:
            return
        self.log_function(
            {
                "type": "tool_end",
                "output": output,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                **kwargs,
            },
            name="Tool End",
        )

    async def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """代理动作时的回调处理

        契约：
        - 输入：代理动作、运行ID等参数
        - 输出：无
        - 副作用：调用日志函数记录事件
        - 失败语义：如果日志函数为 None，则不执行任何操作
        """
        if self.log_function is None:
            return
        self.log_function(
            {
                "type": "agent_action",
                "action": action,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "tags": tags,
                **kwargs,
            },
            name="Agent Action",
        )

    async def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """代理完成时的回调处理

        契约：
        - 输入：完成信息、运行ID等参数
        - 输出：无
        - 副作用：调用日志函数记录事件
        - 失败语义：如果日志函数为 None，则不执行任何操作
        """
        if self.log_function is None:
            return
        self.log_function(
            {
                "type": "agent_finish",
                "finish": finish,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "tags": tags,
                **kwargs,
            },
            name="Agent Finish",
        )
