from __future__ import annotations

"""
模块名称：TwelveLabs 组件懒加载入口

本模块负责对 TwelveLabs 相关组件进行延迟导入，避免在未使用时加载可选依赖。
主要功能包括：
- 统一暴露 TwelveLabs 组件集合
- 属性访问时按需加载并写回缓存

关键组件：
- `__getattr__`：动态导入与异常封装
- `__dir__`：对外暴露公开符号

设计背景：视频理解与向量库依赖较重，适合惰性加载以降低启动成本。
注意事项：不在白名单的属性将抛 `AttributeError`。
"""

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .convert_astra_results import ConvertAstraToTwelveLabs
    from .pegasus_index import PegasusIndexVideo
    from .split_video import SplitVideoComponent
    from .text_embeddings import TwelveLabsTextEmbeddingsComponent
    from .twelvelabs_pegasus import TwelveLabsPegasus
    from .video_embeddings import TwelveLabsVideoEmbeddingsComponent
    from .video_file import VideoFileComponent

_dynamic_imports = {
    "ConvertAstraToTwelveLabs": "convert_astra_results",
    "PegasusIndexVideo": "pegasus_index",
    "SplitVideoComponent": "split_video",
    "TwelveLabsPegasus": "twelvelabs_pegasus",
    "TwelveLabsTextEmbeddingsComponent": "text_embeddings",
    "TwelveLabsVideoEmbeddingsComponent": "video_embeddings",
    "VideoFileComponent": "video_file",
}

__all__ = [
    "ConvertAstraToTwelveLabs",
    "PegasusIndexVideo",
    "SplitVideoComponent",
    "TwelveLabsPegasus",
    "TwelveLabsTextEmbeddingsComponent",
    "TwelveLabsVideoEmbeddingsComponent",
    "VideoFileComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按属性名延迟导入组件。

    契约：
    - 输入：`attr_name` 必须位于 `__all__`
    - 输出：返回对应类对象，并写入 `globals()` 作为缓存
    - 副作用：触发模块导入
    - 失败语义：导入失败抛 `AttributeError`，包含原始异常信息
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
    """返回公开符号列表，供 `dir()` 与 IDE 补全使用。"""
    return list(__all__)
