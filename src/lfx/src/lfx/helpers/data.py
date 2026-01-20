"""Data/Message/DataFrame 辅助函数。

本模块提供数据清洗、序列化与模板格式化等工具函数。
主要功能包括：
- Document 与 Data 互转
- 文本清洗与安全转换为字符串
- 基于模板生成文本列表或聚合文本
"""

import re
from collections import defaultdict
from typing import Any

import orjson
from fastapi.encoders import jsonable_encoder
from langchain_core.documents import Document

from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


def docs_to_data(documents: list[Document]) -> list[Data]:
    """将 Document 列表转换为 Data 列表。

    契约：输入为 Document 列表；输出为 Data 列表。
    """
    return [Data.from_document(document) for document in documents]


def clean_string(s):
    """清理空行并压缩多余换行。"""
    # 移除空行
    s = re.sub(r"^\s*$", "", s, flags=re.MULTILINE)
    # 将三个以上换行压缩为两个
    return re.sub(r"\n{3,}", "\n\n", s)


def _serialize_data(data: Data) -> str:
    """将 Data 序列化为 JSON 字符串。"""
    # 实现：先转换为可 JSON 化结构
    serializable_data = jsonable_encoder(data.data)
    # 实现：使用 orjson 并开启缩进
    json_bytes = orjson.dumps(serializable_data, option=orjson.OPT_INDENT_2)
    # 实现：包装为 Markdown 代码块
    return "```json\n" + json_bytes.decode("utf-8") + "\n```"


def safe_convert(data: Any, *, clean_data: bool = False) -> str:
    """安全地将输入转换为字符串。

    关键路径（三步）：
    1) 按类型选择序列化策略；
    2) 可选清洗 DataFrame 内容；
    3) 返回可读字符串或抛出异常。
    """
    try:
        if isinstance(data, str):
            return clean_string(data)
        if isinstance(data, Message):
            return data.get_text()
        if isinstance(data, Data):
            return clean_string(_serialize_data(data))
        if isinstance(data, DataFrame):
            if clean_data:
                # 移除空行
                data = data.dropna(how="all")
                # 移除单元格内空行
                data = data.replace(r"^\s*$", "", regex=True)
                # 多个换行压缩为一个
                data = data.replace(r"\n+", "\n", regex=True)

            # 注意：转义管道符避免 Markdown 表格错位
            processed_data = data.replace(r"\|", r"\\|", regex=True)

            return processed_data.to_markdown(index=False)

        return clean_string(str(data))
    except (ValueError, TypeError, AttributeError) as e:
        msg = f"Error converting data: {e!s}"
        raise ValueError(msg) from e


def data_to_text_list(template: str, data: Data | list[Data]) -> tuple[list[str], list[Data]]:
    """使用模板格式化 Data 文本。

    契约：输入为模板字符串与 Data/列表；输出为 (文本列表, 原 Data 列表)。
    失败语义：模板为空或非字符串时抛 `ValueError`/`TypeError`。
    关键路径（三步）：
    1) 规范化输入并构建 Data 列表；
    2) 组合格式化字典并安全渲染；
    3) 返回文本列表与原始 Data。
    """
    # 注意：模板必须为字符串
    if data is None:
        return [], []

    if template is None:
        msg = "Template must be a string, but got None."
        raise ValueError(msg)

    if not isinstance(template, str):
        msg = f"Template must be a string, but got {type(template)}"
        raise TypeError(msg)

    formatted_text: list[str] = []
    processed_data: list[Data] = []

    data_list = [data] if isinstance(data, Data) else data

    data_objects = [item if isinstance(item, Data) else Data(text=str(item)) for item in data_list]

    for data_obj in data_objects:
        format_dict = {}

        if isinstance(data_obj.data, dict):
            format_dict.update(data_obj.data)

            if isinstance(data_obj.data.get("data"), dict):
                format_dict.update(data_obj.data["data"])

            elif format_dict.get("error"):
                format_dict["text"] = format_dict["error"]

        format_dict["data"] = data_obj.data

        safe_dict = defaultdict(str, format_dict)

        try:
            formatted_text.append(template.format_map(safe_dict))
            processed_data.append(data_obj)
        except ValueError as e:
            msg = f"Error formatting template: {e!s}"
            raise ValueError(msg) from e

    return formatted_text, processed_data


def data_to_text(template: str, data: Data | list[Data], sep: str = "\n") -> str:
    r"""将 Data 按模板转换为文本并合并。

    契约：输入为模板与 Data/列表；输出为合并后的字符串。
    """
    # 实现：复用 data_to_text_list
    formatted_text, _ = data_to_text_list(template, data)
    sep = "\n" if sep is None else sep
    return sep.join(formatted_text)
