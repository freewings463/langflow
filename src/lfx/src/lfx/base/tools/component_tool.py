"""
模块名称：组件工具封装

本模块将组件输出封装为 `LangChain` 工具，提供工具化执行与元数据管理。
主要功能包括：
- 将组件输出方法包装为工具函数（同步/异步）
- 生成工具输入 `schema` 与描述
- 基于元数据筛选与改写工具信息

关键组件：`ComponentToolkit`、`_build_output_function`/`_build_output_async_function`
设计背景：组件需要以 `Tool` 形式暴露给 `agent`，但 `UI` 消息与参数结构需统一处理
注意事项：工具名必须符合 `^[a-zA-Z0-9_-]+$`；非 `tool_mode` 输入会触发校验失败
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import pandas as pd
from langchain_core.tools import BaseTool, ToolException
from langchain_core.tools.structured import StructuredTool

from lfx.base.tools.constants import TOOL_OUTPUT_NAME
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.serialization.serialization import serialize

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.callbacks import Callbacks

    from lfx.custom.custom_component.component import Component
    from lfx.events.event_manager import EventManager
    from lfx.inputs.inputs import InputTypes
    from lfx.io import Output
    from lfx.schema.dotdict import dotdict

TOOL_TYPES_SET = {"Tool", "BaseTool", "StructuredTool"}


def _get_input_type(input_: InputTypes):
    """解析输入类型显示值。
    契约：优先返回 `input_types`，缺失时回退 `field_type`。
    """
    if input_.input_types:
        if len(input_.input_types) == 1:
            return input_.input_types[0]
        return " | ".join(input_.input_types)
    return input_.field_type


def build_description(component: Component) -> str:
    """构建组件描述文本。
    契约：返回组件 `description`，缺失时返回空字符串。
    关键路径：读取 `description` → 空值兜底。
    决策：空描述返回空字符串。问题：避免 `None` 影响工具描述；方案：空串兜底；代价：信息缺失；重评：当必须强制描述时。
    """
    return component.description or ""


async def send_message_noop(
    message: Message,
    id_: str | None = None,  # noqa: ARG001
    *,
    skip_db_update: bool = False,  # noqa: ARG001
) -> Message:
    """空实现的 send_message，用于屏蔽 UI 消息发送。
    契约：直接返回入参 `message`。
    关键路径：不做处理直接回传。
    决策：返回原消息而不落库。问题：工具执行不应污染 `UI`；方案：no-op；代价：消息不可追踪；重评：当需要审计工具调用时。
    """
    return message


def patch_components_send_message(component: Component):
    """临时替换组件的 send_message。
    契约：返回原始 send_message 以便恢复。
    关键路径：保存原方法 → 替换为 no-op → 返回原方法。
    决策：直接替换实例方法。问题：工具执行时不应向 `UI` 发消息；方案：短期替换；代价：并发下可能混淆；重评：当引入上下文级消息控制时。
    """
    old_send_message = component.send_message
    component.send_message = send_message_noop  # type: ignore[method-assign, assignment]
    return old_send_message


def _patch_send_message_decorator(component, func):
    """为函数执行期间临时替换 send_message 的装饰器。

    适用场景：组件作为工具执行时，避免向 UI 输出消息。
    """

    async def async_wrapper(*args, **kwargs):
        original_send_message = component.send_message
        component.send_message = send_message_noop
        try:
            return await func(*args, **kwargs)
        finally:
            component.send_message = original_send_message

    def sync_wrapper(*args, **kwargs):
        original_send_message = component.send_message
        component.send_message = send_message_noop
        try:
            return func(*args, **kwargs)
        finally:
            component.send_message = original_send_message

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def _build_output_function(component: Component, output_method: Callable, event_manager: EventManager | None = None):
    """构建同步工具执行函数。
    契约：返回可被工具调用的函数，异常统一包装为 `ToolException`。
    决策：捕获所有异常并转为 `ToolException`。问题：`LangChain` 需要统一错误类型；方案：统一包装；代价：丢失异常类型；重评：当需要精细异常分类时。
    """
    def output_function(*args, **kwargs):
        try:
            if event_manager:
                event_manager.on_build_start(data={"id": component.get_id()})
            component.set(*args, **kwargs)
            result = output_method()
            if event_manager:
                event_manager.on_build_end(data={"id": component.get_id()})
        except Exception as e:
            raise ToolException(e) from e

        if isinstance(result, Message):
            return result.get_text()
        if isinstance(result, Data):
            return result.data
        # 注意：不调用 model_dump，避免不可序列化对象。
        return serialize(result)

    return _patch_send_message_decorator(component, output_function)


def _build_output_async_function(
    component: Component, output_method: Callable, event_manager: EventManager | None = None
):
    """构建异步工具执行函数。
    契约：返回可 await 的工具函数，异常统一包装为 `ToolException`。
    决策：事件回调用线程包装。问题：避免阻塞事件循环；方案：`to_thread`；代价：线程切换开销；重评：当事件回调变为 async 时。
    """
    async def output_function(*args, **kwargs):
        try:
            if event_manager:
                await asyncio.to_thread(event_manager.on_build_start, data={"id": component.get_id()})
            component.set(*args, **kwargs)
            result = await output_method()
            if event_manager:
                await asyncio.to_thread(event_manager.on_build_end, data={"id": component.get_id()})
        except Exception as e:
            raise ToolException(e) from e
        if isinstance(result, Message):
            return result.get_text()
        if isinstance(result, Data):
            return result.data
        # 注意：不调用 model_dump，避免不可序列化对象。
        return serialize(result)

    return _patch_send_message_decorator(component, output_function)


def _format_tool_name(name: str):
    """格式化工具名为安全字符集。"""
    # 注意：仅保留字母、数字、下划线与短横线。
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name)


def _add_commands_to_tool_description(tool_description: str, commands: str):
    """将命令提示拼接到工具描述中。"""
    return f"very_time you see one of those commands {commands} run the tool. tool description is {tool_description}"


class ComponentToolkit:
    """组件工具集封装，负责从组件输出生成工具。
    契约：根据组件 `outputs` 生成 `StructuredTool` 列表并应用元数据。
    关键路径：过滤输出 → 构建 `schema` → 生成工具 → 元数据覆盖。
    决策：输出方法名作为工具名基准。问题：保证稳定可追踪；方案：方法名+格式化；代价：可读性一般；重评：当需要更友好展示名时。
    """

    def __init__(self, component: Component, metadata: pd.DataFrame | None = None):
        self.component = component
        self.metadata = metadata

    def _should_skip_output(self, output: Output) -> bool:
        """判断输出是否应跳过工具化。
        契约：返回 True 表示跳过。
        决策：过滤 TOOL_OUTPUT_NAME 与工具类型输出。问题：避免递归与重复工具；方案：白名单过滤；代价：部分输出不可工具化；重评：当支持嵌套工具时。
        """
        return not output.tool_mode or (
            output.name == TOOL_OUTPUT_NAME or any(tool_type in output.types for tool_type in TOOL_TYPES_SET)
        )

    def get_tools(
        self,
        tool_name: str | None = None,
        tool_description: str | None = None,
        callbacks: Callbacks | None = None,
        flow_mode_inputs: list[dotdict] | None = None,
    ) -> list[BaseTool]:
        """生成工具列表。
        契约：返回 `StructuredTool` 列表；若输入不满足 `tool_mode` 则抛 `ValueError`。
        关键路径：选择 `outputs` → 构建 `args_schema` → 生成 `StructuredTool` → 应用命名规则。
        决策：缺少 `tool_mode` 必填输入时直接失败。问题：工具调用会必然报错；方案：提前阻断；代价：工具不可用；重评：当支持运行期补齐时。
        """
        from lfx.io.schema import create_input_schema, create_input_schema_from_dict

        tools = []
        for output in self.component.outputs:
            if self._should_skip_output(output):
                continue

            if not output.method:
                msg = f"Output {output.name} does not have a method defined"
                raise ValueError(msg)

            output_method: Callable = getattr(self.component, output.method)
            args_schema = None
            tool_mode_inputs = [_input for _input in self.component.inputs if getattr(_input, "tool_mode", False)]
            if flow_mode_inputs:
                args_schema = create_input_schema_from_dict(
                    inputs=flow_mode_inputs,
                    param_key="flow_tweak_data",
                )
            elif tool_mode_inputs:
                args_schema = create_input_schema(tool_mode_inputs)
            elif output.required_inputs:
                inputs = [
                    self.component.get_underscore_inputs()[input_name]
                    for input_name in output.required_inputs
                    if getattr(self.component, input_name) is None
                ]
                # 注意：必填输入若非 `tool_mode`，工具调用会失败，需提前阻断。
                # TODO：此逻辑可能需改进，例如必填项是 `API Key`。
                if not all(getattr(_input, "tool_mode", False) for _input in inputs):
                    non_tool_mode_inputs = [
                        input_.name
                        for input_ in inputs
                        if not getattr(input_, "tool_mode", False) and input_.name is not None
                    ]
                    non_tool_mode_inputs_str = ", ".join(non_tool_mode_inputs)
                    msg = (
                        f"Output '{output.name}' requires inputs that are not in tool mode. "
                        f"The following inputs are not in tool mode: {non_tool_mode_inputs_str}. "
                        "Please ensure all required inputs are set to tool mode."
                    )
                    raise ValueError(msg)
                args_schema = create_input_schema(inputs)

            else:
                args_schema = create_input_schema(self.component.inputs)

            name = f"{output.method}".strip(".")
            formatted_name = _format_tool_name(name)
            event_manager = self.component.get_event_manager()
            if asyncio.iscoroutinefunction(output_method):
                tools.append(
                    StructuredTool(
                        name=formatted_name,
                        description=build_description(self.component),
                        coroutine=_build_output_async_function(self.component, output_method, event_manager),
                        args_schema=args_schema,
                        handle_tool_error=True,
                        callbacks=callbacks,
                        tags=[formatted_name],
                        metadata={
                            "display_name": formatted_name,
                            "display_description": build_description(self.component),
                        },
                    )
                )
            else:
                tools.append(
                    StructuredTool(
                        name=formatted_name,
                        description=build_description(self.component),
                        func=_build_output_function(self.component, output_method, event_manager),
                        args_schema=args_schema,
                        handle_tool_error=True,
                        callbacks=callbacks,
                        tags=[formatted_name],
                        metadata={
                            "display_name": formatted_name,
                            "display_description": build_description(self.component),
                        },
                    )
                )
        if len(tools) == 1 and (tool_name or tool_description):
            tool = tools[0]
            tool.name = _format_tool_name(str(tool_name)) or tool.name
            tool.description = tool_description or tool.description
            tool.tags = [tool.name]
        elif flow_mode_inputs and (tool_name or tool_description):
            for tool in tools:
                tool.name = _format_tool_name(str(tool_name) + "_" + str(tool.name)) or tool.name
                tool.description = (
                    str(tool_description) + " Output details: " + str(tool.description)
                ) or tool.description
                tool.tags = [tool.name]
        elif tool_name or tool_description:
            msg = (
                "When passing a tool name or description, there must be only one tool, "
                f"but {len(tools)} tools were found."
            )
            raise ValueError(msg)
        return tools

    def get_tools_metadata_dictionary(self) -> dict:
        """将元数据表转换为以 tag 为键的字典。
        契约：仅保留含 `tags` 的记录；解析异常抛 `ValueError`。
        关键路径：`DataFrame` → records → 按 `tags[0]` 建索引。
        决策：以 `tags[0]` 作为键。问题：工具唯一标识需要稳定键；方案：首个 tag；代价：多 tag 未使用；重评：当支持多 tag 索引时。
        """
        if isinstance(self.metadata, pd.DataFrame):
            try:
                return {
                    record["tags"][0]: record
                    for record in self.metadata.to_dict(orient="records")
                    if record.get("tags")
                }
            except (KeyError, IndexError) as e:
                msg = "Error processing metadata records: " + str(e)
                raise ValueError(msg) from e
        return {}

    def update_tools_metadata(
        self,
        tools: list[BaseTool | StructuredTool],
    ) -> list[BaseTool]:
        """根据元数据更新工具名称与描述。
        契约：返回过滤后的工具列表；`status=False` 的工具会被剔除。
        关键路径：构建元数据索引 → 遍历工具 → 覆盖名称/描述 → 过滤返回。
        决策：仅保留 `status=True` 的工具。问题：需要可控启用/禁用；方案：元数据过滤；代价：工具数量变化；重评：当需要软禁用而非删除时。
        """
        # 注意：按元数据中的 `name`/`description`/`commands` 覆盖工具信息。
        if isinstance(self.metadata, pd.DataFrame):
            metadata_dict = self.get_tools_metadata_dictionary()
            filtered_tools = []
            for tool in tools:
                if isinstance(tool, StructuredTool | BaseTool) and tool.tags:
                    try:
                        tag = tool.tags[0]
                    except IndexError:
                        msg = "Tool tags cannot be empty."
                        raise ValueError(msg) from None
                    if tag in metadata_dict:
                        tool_metadata = metadata_dict[tag]
                        # 注意：仅保留 `status=True` 的工具
                        if tool_metadata.get("status", True):
                            tool.name = tool_metadata.get("name", tool.name)
                            tool.description = tool_metadata.get("description", tool.description)
                            if tool_metadata.get("commands"):
                                tool.description = _add_commands_to_tool_description(
                                    tool.description, tool_metadata.get("commands")
                                )
                            filtered_tools.append(tool)
                else:
                    msg = f"Expected a StructuredTool or BaseTool, got {type(tool)}"
                    raise TypeError(msg)
            return filtered_tools
        return tools
