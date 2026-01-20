"""
模块名称：模型元数据结构

本模块定义统一的模型元数据结构与构建函数，主要用于在各模型常量文件中
保持字段一致性。
主要功能包括：
- 定义 `ModelMetadata` 字段集合与默认语义
- 提供 `create_model_metadata` 构造函数

关键组件：
- `ModelMetadata`：模型元数据 TypedDict
- `create_model_metadata`：元数据构建器

设计背景：统一字段命名，避免各模型列表格式分裂。
注意事项：字段默认值通过构造函数显式设置，不依赖调用方填充。
"""

from typing import TypedDict


class ModelMetadata(TypedDict, total=False):
    """模型元数据结构（可选字段）。"""

    provider: str  # 提供方名称（如 "anthropic" / "groq" / "openai"）
    name: str  # 模型名称/ID
    icon: str  # UI 图标名称
    tool_calling: bool  # 是否支持工具调用（默认 False）
    reasoning: bool  # 是否为推理模型（默认 False）
    search: bool  # 是否为搜索模型（默认 False）
    preview: bool  # 是否为预览/测试模型（默认 False）
    not_supported: bool  # 是否不受支持（默认 False）
    deprecated: bool  # 是否已弃用（默认 False）
    default: bool  # 是否为推荐默认（默认 False）
    model_type: str  # 模型类型（默认 "llm" 或 "embeddings"）


def create_model_metadata(
    provider: str,
    name: str,
    icon: str,
    *,
    tool_calling: bool = False,
    reasoning: bool = False,
    search: bool = False,
    preview: bool = False,
    not_supported: bool = False,
    deprecated: bool = False,
    default: bool = False,
    model_type: str = "llm",
) -> ModelMetadata:
    """构造标准化的模型元数据。

    契约：返回包含完整默认值的 `ModelMetadata` 字典。
    副作用：无。
    失败语义：类型不匹配由调用方负责（TypedDict 不做运行时校验）。
    """
    return ModelMetadata(
        provider=provider,
        name=name,
        icon=icon,
        tool_calling=tool_calling,
        reasoning=reasoning,
        search=search,
        preview=preview,
        not_supported=not_supported,
        deprecated=deprecated,
        default=default,
        model_type=model_type,
    )
