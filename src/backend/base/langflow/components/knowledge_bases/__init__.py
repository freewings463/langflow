"""模块名称：知识库组件兼容入口

模块目的：为旧路径 `langflow.components.knowledge_bases` 提供向后兼容入口。
主要功能：
- 将导入重定向到 `lfx.components.files_and_knowledge`
- 维持旧子模块路径可用（`ingestion`/`retrieval`）
使用场景：历史代码仍使用旧包路径时的兼容访问。
关键组件：`_redirected_submodules`、`_RedirectedModule`、`__getattr__`
设计背景：知识库组件迁移至 `lfx.components.files_and_knowledge`。
注意事项：重定向模块通过 `sys.modules` 注入，首次访问才实际导入。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types

from lfx.components.files_and_knowledge import __all__ as _lfx_all

__all__: list[str] = list(_lfx_all)

# 注意：向 `sys.modules` 注册重定向子模块，兼容 `importlib.import_module` 直连路径。
_redirected_submodules = {
    "langflow.components.knowledge_bases.ingestion": "lfx.components.files_and_knowledge.ingestion",
    "langflow.components.knowledge_bases.retrieval": "lfx.components.files_and_knowledge.retrieval",
}

for old_path, new_path in _redirected_submodules.items():
    if old_path not in sys.modules:
        # 实现：懒加载代理，首次访问时导入真实模块并注册回原路径
        class _RedirectedModule:
            _module: types.ModuleType | None

            def __init__(self, target_path: str, original_path: str):
                self._target_path = target_path
                self._original_path = original_path
                self._module = None

            def __getattr__(self, name: str) -> Any:
                """延迟导入真实模块并转发属性访问。"""
                if self._module is None:
                    from importlib import import_module

                    self._module = import_module(self._target_path)
                    # 注意：将真实模块注册回原路径，保持后续导入一致性
                    sys.modules[self._original_path] = self._module
                return getattr(self._module, name)

            def __repr__(self) -> str:
                return f"<redirected module '{self._original_path}' -> '{self._target_path}'>"

        sys.modules[old_path] = _RedirectedModule(new_path, old_path)  # type: ignore[assignment]


def __getattr__(attr_name: str) -> Any:
    """将属性访问转发到 `lfx.components.files_and_knowledge`。"""
    # 兼容旧子模块访问
    if attr_name == "ingestion":
        from importlib import import_module

        result = import_module("lfx.components.files_and_knowledge.ingestion")
        globals()[attr_name] = result
        return result
    if attr_name == "retrieval":
        from importlib import import_module

        result = import_module("lfx.components.files_and_knowledge.retrieval")
        globals()[attr_name] = result
        return result

    from lfx.components import files_and_knowledge

    return getattr(files_and_knowledge, attr_name)


def __dir__() -> list[str]:
    """返回对外可见的导出符号列表。"""
    return list(__all__)
