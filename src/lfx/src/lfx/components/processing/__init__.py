"""处理类组件导出入口。

本模块集中导出 Processing 相关组件，并通过延迟导入避免不必要的依赖加载。
注意事项：仅当访问对应组件属性时才会触发实际导入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.processing.combine_text import CombineTextComponent
    from lfx.components.processing.converter import TypeConverterComponent
    from lfx.components.processing.create_list import CreateListComponent
    from lfx.components.processing.data_operations import DataOperationsComponent
    from lfx.components.processing.dataframe_operations import DataFrameOperationsComponent
    from lfx.components.processing.json_cleaner import JSONCleaner
    from lfx.components.processing.output_parser import OutputParserComponent
    from lfx.components.processing.parse_data import ParseDataComponent
    from lfx.components.processing.parser import ParserComponent
    from lfx.components.processing.regex import RegexExtractorComponent
    from lfx.components.processing.split_text import SplitTextComponent
    from lfx.components.processing.store_message import MessageStoreComponent

_dynamic_imports = {
    "CombineTextComponent": "combine_text",
    "TypeConverterComponent": "converter",
    "CreateListComponent": "create_list",
    "DataOperationsComponent": "data_operations",
    "DataFrameOperationsComponent": "dataframe_operations",
    "JSONCleaner": "json_cleaner",
    "OutputParserComponent": "output_parser",
    "ParseDataComponent": "parse_data",
    "ParserComponent": "parser",
    "RegexExtractorComponent": "regex",
    "SplitTextComponent": "split_text",
    "MessageStoreComponent": "store_message",
}

__all__ = [
    "CombineTextComponent",
    "CreateListComponent",
    "DataFrameOperationsComponent",
    "DataOperationsComponent",
    "JSONCleaner",
    "MessageStoreComponent",
    "OutputParserComponent",
    "ParseDataComponent",
    "ParserComponent",
    "RegexExtractorComponent",
    "SplitTextComponent",
    "TypeConverterComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入处理组件并缓存到模块命名空间。

    契约：输入为组件名字符串；输出对应组件对象。
    失败语义：未注册组件名或导入失败时抛 `AttributeError`。
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
    """返回模块对外暴露的组件列表。"""
    return list(__all__)
