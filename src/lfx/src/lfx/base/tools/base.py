"""
模块名称：工具基础能力

本模块提供工具状态摘要构建能力，用于将 `Tool` 的名称、描述与参数结构
组织为可读字符串，便于 `UI`/日志展示。主要功能包括：
- 拼接工具名称与描述
- 输出参数描述与 args_schema 概览

关键组件：build_status_from_tool
设计背景：需要统一的工具状态文本格式，避免各处拼接不一致
注意事项：仅输出有 `description` 的参数
"""

from lfx.field_typing import Tool


def build_status_from_tool(tool: Tool):
    """构建工具状态字符串。
    契约：返回包含名称、描述、参数列表与 `args_schema` 的可读文本。
    关键路径：抽取描述 → 拼接参数描述 → 追加 schema 信息。
    决策：始终输出 `args_schema` 概览。问题：排障时需知道参数结构；方案：追加 `repr`；代价：文本变长；重评：当 `UI` 以结构化呈现替代时。
    """
    description_repr = repr(tool.description).strip("'")
    args_str = "\n".join(
        [
            f"- {arg_name}: {arg_data['description']}"
            for arg_name, arg_data in tool.args.items()
            if "description" in arg_data
        ]
    )
    # 注意：包含 args_schema 便于排障。
    args_schema_str = repr(tool.args_schema) if tool.args_schema else "None"
    status = f"Name: {tool.name}\nDescription: {description_repr}"
    status += f"\nArgs Schema: {args_schema_str}"
    return status + (f"\nArguments:\n{args_str}" if args_str else "")
