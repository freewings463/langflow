"""
模块名称：lfx.components.assemblyai

本模块提供 AssemblyAI 组件的懒加载入口，统一对外导出组件符号。
主要功能包括：
- 按需加载组件实现，降低导入成本
- 维持稳定的组件导入路径

关键组件：
- `__getattr__`：按名称懒加载组件
- `_dynamic_imports`：组件名到模块名映射

设计背景：组件数量增多时，需降低启动时导入负担
使用场景：组件注册与动态加载
注意事项：属性名不在映射中将抛出 `AttributeError`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .assemblyai_get_subtitles import AssemblyAIGetSubtitles
    from .assemblyai_lemur import AssemblyAILeMUR
    from .assemblyai_list_transcripts import AssemblyAIListTranscripts
    from .assemblyai_poll_transcript import AssemblyAITranscriptionJobPoller
    from .assemblyai_start_transcript import AssemblyAITranscriptionJobCreator

_dynamic_imports = {
    "AssemblyAIGetSubtitles": "assemblyai_get_subtitles",
    "AssemblyAILeMUR": "assemblyai_lemur",
    "AssemblyAIListTranscripts": "assemblyai_list_transcripts",
    "AssemblyAITranscriptionJobCreator": "assemblyai_start_transcript",
    "AssemblyAITranscriptionJobPoller": "assemblyai_poll_transcript",
}

__all__ = [
    "AssemblyAIGetSubtitles",
    "AssemblyAILeMUR",
    "AssemblyAIListTranscripts",
    "AssemblyAITranscriptionJobCreator",
    "AssemblyAITranscriptionJobPoller",
]


def __getattr__(attr_name: str) -> Any:
    """按属性名懒加载 AssemblyAI 组件。

    契约：`attr_name` 必须在 `_dynamic_imports` 中。
    副作用：动态导入模块并缓存到 `globals()`。
    失败语义：模块导入失败抛 `AttributeError`。
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """暴露可用组件名列表。"""
    return list(__all__)
