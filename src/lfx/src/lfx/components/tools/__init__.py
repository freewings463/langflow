"""
模块名称：工具组件懒加载入口

本模块提供工具类组件的按需导入，用于降低启动成本并保持导入路径稳定。
主要功能包括：
- 组件名到模块名映射
- 属性访问时延迟导入并缓存
- 统一导出 `__all__` 与 `__dir__`

关键组件：
- `_dynamic_imports`：组件名映射表
- `__getattr__`：懒加载入口
- `__dir__`：导出成员列表

设计背景：工具组件依赖多、启动慢，需要按需加载以减少开销。
注意事项：仅支持映射表内的组件名，其他访问会抛 `AttributeError`。
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from langchain_core._api.deprecation import LangChainDeprecationWarning

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .calculator import CalculatorToolComponent
    from .python_code_structured_tool import PythonCodeStructuredTool
    from .python_repl import PythonREPLToolComponent
    from .search_api import SearchAPIComponent
    from .searxng import SearXNGToolComponent
    from .serp_api import SerpAPIComponent
    from .tavily_search_tool import TavilySearchToolComponent
    from .wikidata_api import WikidataAPIComponent
    from .wikipedia_api import WikipediaAPIComponent
    from .yahoo_finance import YfinanceToolComponent

_dynamic_imports = {
    "CalculatorToolComponent": "calculator",
    "PythonCodeStructuredTool": "python_code_structured_tool",
    "PythonREPLToolComponent": "python_repl",
    "SearchAPIComponent": "search_api",
    "SearXNGToolComponent": "searxng",
    "SerpAPIComponent": "serp_api",
    "TavilySearchToolComponent": "tavily_search_tool",
    "WikidataAPIComponent": "wikidata_api",
    "WikipediaAPIComponent": "wikipedia_api",
    "YfinanceToolComponent": "yahoo_finance",
}

__all__ = [
    "CalculatorToolComponent",
    "PythonCodeStructuredTool",
    "PythonREPLToolComponent",
    "SearXNGToolComponent",
    "SearchAPIComponent",
    "SerpAPIComponent",
    "TavilySearchToolComponent",
    "WikidataAPIComponent",
    "WikipediaAPIComponent",
    "YfinanceToolComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按名称懒加载组件并缓存到模块全局。

    契约：输入组件类名，返回对应类对象。
    失败语义：名称不存在或导入失败时抛 `AttributeError`。
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        with warnings.catch_warnings():
            # 注意：忽略 LangChain 旧接口警告，避免懒加载噪声。
            warnings.simplefilter("ignore", LangChainDeprecationWarning)
            result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """返回可见成员列表，保持反射与自动补全一致。"""
    return list(__all__)
