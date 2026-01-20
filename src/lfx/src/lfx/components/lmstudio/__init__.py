from __future__ import annotations

"""
模块名称：LM Studio 组件懒加载入口

本模块负责对 LM Studio 相关组件进行延迟导入，避免在未使用时触发可选依赖加载。
主要功能包括：
- 统一暴露 `LMStudioEmbeddingsComponent` 与 `LMStudioModelComponent`
- 在属性访问时按需导入对应子模块
- 将加载后的对象写回全局缓存以减少重复导入

关键组件：
- `__getattr__`：延迟导入与错误包装
- `__dir__`：向 IDE/补全暴露可用符号

设计背景：组件依赖可能缺失或较重，需在实际使用时才加载。
注意事项：属性名不在白名单时抛出 `AttributeError`。
"""

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.lmstudio.lmstudioembeddings import LMStudioEmbeddingsComponent
    from lfx.components.lmstudio.lmstudiomodel import LMStudioModelComponent

_dynamic_imports = {
    "LMStudioEmbeddingsComponent": "lmstudioembeddings",
    "LMStudioModelComponent": "lmstudiomodel",
}

__all__ = ["LMStudioEmbeddingsComponent", "LMStudioModelComponent"]


def __getattr__(attr_name: str) -> Any:
    """按属性名延迟导入组件。

    契约：
    - 输入：`attr_name` 必须是公开组件名（见 `__all__`）
    - 输出：返回对应类对象，并写入 `globals()` 作为缓存
    - 副作用：触发动态导入；成功后修改模块全局命名空间
    - 失败语义：缺失模块/依赖时抛 `AttributeError`，消息包含原始异常
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
    """返回可公开的符号列表，供 `dir()` 与补全使用。"""
    return list(__all__)
