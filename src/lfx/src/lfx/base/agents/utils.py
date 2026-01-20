"""
模块名称：代理构建与工具辅助

本模块提供代理创建辅助函数、注册表与缓存安全访问工具，主要用于统一代理构建、
聊天历史处理与配置整理。
主要功能包括：
- 各类 `LangChain` 代理构建器的校验包装
- 消息/数据转换与空内容过滤
- 代理注册表与缓存安全访问
- 扁平字典反解与发送者名称解析

关键组件：
- `AgentSpec`：代理规范
- `AGENTS`：代理注册表
- `data_to_messages`：历史消息转换与过滤

设计背景：多代理类型与缓存系统需要统一入口。
注意事项：空内容会被过滤以避免模型输入错误。
"""

import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import (
    create_json_chat_agent,
    create_openai_tools_agent,
    create_tool_calling_agent,
    create_xml_agent,
)
from langchain.agents.xml.base import render_text_description
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import BaseMessage
from langchain_core.prompts import BasePromptTemplate, ChatPromptTemplate
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.services.cache.base import CacheService
from lfx.services.cache.utils import CacheMiss

from .default_prompts import XML_AGENT_PROMPT


class AgentSpec(BaseModel):
    """代理规范类，定义代理的配置和行为

    契约：
    - 输入：函数、提示、字段列表和仓库信息
    - 输出：AgentSpec 实例
    - 副作用：定义代理的行为规范
    - 失败语义：如果验证失败，抛出 Pydantic 验证错误
    """
    func: Callable[
        [
            BaseLanguageModel,
            Sequence[BaseTool],
            BasePromptTemplate | ChatPromptTemplate,
            Callable[[list[BaseTool]], str] | None,
            bool | list[str] | None,
        ],
        Any,
    ]
    prompt: Any | None = None
    fields: list[str]
    hub_repo: str | None = None


def data_to_messages(data: list[Data | Message]) -> list[BaseMessage]:
    """将数据列表转换为消息列表

    关键路径（三步）：
    1) 遍历数据列表
    2) 将每个数据项转换为 LangChain 消息
    3) 过滤掉空内容的消息

    异常流：转换失败时记录警告并跳过该项目。
    性能瓶颈：大量数据转换时。
    排障入口：日志关键字 "Skipping message with empty content"、"Failed to convert message"。
    
    契约：
    - 输入：Data 或 Message 对象列表
    - 输出：BaseMessage 对象列表
    - 副作用：过滤掉空内容的消息
    - 失败语义：如果转换失败，跳过该项并记录警告
    """
    messages = []
    for value in data:
        try:
            lc_message = value.to_lc_message()
            # 注意：仅保留非空内容，避免 `Anthropic API` 输入错误
            content = lc_message.content
            if content and ((isinstance(content, str) and content.strip()) or (isinstance(content, list) and content)):
                messages.append(lc_message)
            else:
                logger.warning("Skipping message with empty content in chat history")
        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to convert message to BaseMessage: {e}")
            continue
    return messages


def validate_and_create_xml_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: BasePromptTemplate,
    tools_renderer: Callable[[list[BaseTool]], str] = render_text_description,
    *,
    stop_sequence: bool | list[str] = True,
):
    """验证并创建 XML 代理

    契约：
    - 输入：语言模型、工具序列、提示等
    - 输出：XML 代理实例
    - 副作用：无
    - 失败语义：如果创建失败，抛出相应异常
    """
    return create_xml_agent(
        llm=llm,
        tools=tools,
        prompt=prompt,
        tools_renderer=tools_renderer,
        stop_sequence=stop_sequence,
    )


def validate_and_create_openai_tools_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate,
    _tools_renderer: Callable[[list[BaseTool]], str] = render_text_description,
    *,
    _stop_sequence: bool | list[str] = True,
):
    """验证并创建 OpenAI 工具代理

    契约：
    - 输入：语言模型、工具序列、提示等
    - 输出：OpenAI 工具代理实例
    - 副作用：无
    - 失败语义：如果创建失败，抛出相应异常
    """
    return create_openai_tools_agent(
        llm=llm,
        tools=tools,
        prompt=prompt,
    )


def validate_and_create_tool_calling_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate,
    _tools_renderer: Callable[[list[BaseTool]], str] = render_text_description,
    *,
    _stop_sequence: bool | list[str] = True,
):
    """验证并创建工具调用代理

    契约：
    - 输入：语言模型、工具序列、提示等
    - 输出：工具调用代理实例
    - 副作用：无
    - 失败语义：如果创建失败，抛出相应异常
    """
    return create_tool_calling_agent(
        llm=llm,
        tools=tools,
        prompt=prompt,
    )


def validate_and_create_json_chat_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate,
    tools_renderer: Callable[[list[BaseTool]], str] = render_text_description,
    *,
    stop_sequence: bool | list[str] = True,
):
    """验证并创建 JSON 聊天代理

    契约：
    - 输入：语言模型、工具序列、提示等
    - 输出：JSON 聊天代理实例
    - 副作用：无
    - 失败语义：如果创建失败，抛出相应异常
    """
    return create_json_chat_agent(
        llm=llm,
        tools=tools,
        prompt=prompt,
        tools_renderer=tools_renderer,
        stop_sequence=stop_sequence,
    )


# 代理注册表：定义可用的代理类型及其规范
AGENTS: dict[str, AgentSpec] = {
    "Tool Calling Agent": AgentSpec(
        func=validate_and_create_tool_calling_agent,
        prompt=None,
        fields=["llm", "tools", "prompt"],
        hub_repo=None,
    ),
    "XML Agent": AgentSpec(
        func=validate_and_create_xml_agent,
        prompt=XML_AGENT_PROMPT,  # Ensure XML_AGENT_PROMPT is properly defined and typed.
        fields=["llm", "tools", "prompt", "tools_renderer", "stop_sequence"],
        hub_repo="hwchase17/xml-agent-convo",
    ),
    "OpenAI Tools Agent": AgentSpec(
        func=validate_and_create_openai_tools_agent,
        prompt=None,
        fields=["llm", "tools", "prompt"],
        hub_repo=None,
    ),
    "JSON Chat Agent": AgentSpec(
        func=validate_and_create_json_chat_agent,
        prompt=None,
        fields=["llm", "tools", "prompt", "tools_renderer", "stop_sequence"],
        hub_repo="hwchase17/react-chat-json",
    ),
}


def get_agents_list():
    """获取可用代理列表

    契约：
    - 输入：无
    - 输出：代理名称列表
    - 副作用：无
    - 失败语义：无
    """
    return list(AGENTS.keys())


def safe_cache_get(cache: CacheService, key, default=None):
    """安全地从缓存获取值，处理 CacheMiss 对象

    契约：
    - 输入：缓存服务、键和默认值
    - 输出：缓存值或默认值
    - 副作用：无
    - 失败语义：如果获取失败，返回默认值
    """
    try:
        value = cache.get(key)
        if isinstance(value, CacheMiss):
            return default
    except (AttributeError, KeyError, TypeError):
        return default
    else:
        return value


def safe_cache_set(cache: CacheService, key, value):
    """安全地设置缓存值，处理潜在错误

    契约：
    - 输入：缓存服务、键和值
    - 输出：无
    - 副作用：向缓存设置值
    - 失败语义：如果设置失败，记录警告日志
    """
    try:
        cache.set(key, value)
    except (AttributeError, TypeError) as e:
        logger.warning(f"Failed to set cache key '{key}': {e}")


def maybe_unflatten_dict(flat: dict[str, Any]) -> dict[str, Any]:
    """如果任何键看起来是嵌套的（包含点或"[index]"），则重建嵌套结构；
    否则返回扁平字典。

    关键路径（三步）：
    1) 检查是否有嵌套键
    2) 如果有，解析键路径
    3) 重建嵌套结构

    异常流：无。
    性能瓶颈：大量嵌套键处理时。
    排障入口：无。
    
    契约：
    - 输入：扁平字典
    - 输出：可能是嵌套的字典
    - 副作用：无
    - 失败语义：如果解析失败，返回原始字典
    """
    # 快速检查：是否存在嵌套键
    if not any(re.search(r"\.|\[\d+\]", key) for key in flat):
        return flat

    # 否则展开为嵌套字典/列表
    nested: dict[str, Any] = {}
    array_re = re.compile(r"^(.+)\[(\d+)\]$")

    for key, val in flat.items():
        parts = key.split(".")
        cur = nested
        for i, part in enumerate(parts):
            m = array_re.match(part)
            # 数组片段
            if m:
                name, idx = m.group(1), int(m.group(2))
                lst = cur.setdefault(name, [])
                # 保证列表长度足够
                while len(lst) <= idx:
                    lst.append({})
                if i == len(parts) - 1:
                    lst[idx] = val
                else:
                    cur = lst[idx]
            # 普通对象键
            elif i == len(parts) - 1:
                cur[part] = val
            else:
                cur = cur.setdefault(part, {})

    return nested


def get_chat_output_sender_name(self) -> str | None:
    """从 ChatOutput 组件获取发送者名称

    契约：
    - 输入：包含图结构的对象
    - 输出：发送者名称字符串或 None
    - 副作用：无
    - 失败语义：如果获取失败，返回 None
    """
    if not hasattr(self, "graph") or not self.graph:
        return None

    # 注意：`PlaceholderGraph` 无 `vertices` 属性
    if not hasattr(self.graph, "vertices"):
        return None

    for vertex in self.graph.vertices:
        # 注意：需同时校验 `data`/`type`/`raw_params` 的存在与类型
        if (
            hasattr(vertex, "data")
            and vertex.data.get("type") == "ChatOutput"
            and hasattr(vertex, "raw_params")
            and vertex.raw_params
        ):
            return vertex.raw_params.get("sender_name")

    return None
