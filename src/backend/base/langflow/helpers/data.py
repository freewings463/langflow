"""
模块名称：Data 与文本/表格转换工具

本模块提供 Data、Message、Document 与文本/表格之间的转换与序列化能力。
主要功能包括：
- 文档/消息到 Data 或文本的转换
- 按模板格式化 Data
- 安全字符串化与 DataFrame 输出

关键组件：
- `data_to_text_list` / `data_to_text`
- `safe_convert`
- `_serialize_data`

设计背景：组件输出类型多样，需要统一的文本化与序列化路径。
注意事项：模板格式化会静默忽略缺失字段。
"""

import re
from collections import defaultdict
from typing import Any

import orjson
from fastapi.encoders import jsonable_encoder
from langchain_core.documents import Document
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame

from langflow.schema.message import Message


def docs_to_data(documents: list[Document]) -> list[Data]:
    """将 Document 列表转换为 Data 列表。

    契约：按顺序调用 `Data.from_document`。
    失败语义：转换失败向上抛异常。

    决策：统一通过 `Data.from_document` 转换
    问题：Document 元数据结构不一致
    方案：集中由 Data 负责解析
    代价：转换规则固定在 Data 内部
    重评：若需要多种转换策略再扩展
    """
    return [Data.from_document(document) for document in documents]


def data_to_text_list(template: str, data: Data | list[Data]) -> tuple[list[str], list[Data]]:
    """按模板将 Data 格式化为文本列表并保留原数据。

    契约：返回 `(文本列表, Data列表)`；`data=None` 返回空列表。
    关键路径（三步）：
    1) 统一 `data` 为 `Data` 列表
    2) 构造 `format_dict`（含嵌套 `data`）
    3) 执行 `format_map` 并收集结果
    失败语义：模板为空或类型不符抛 `ValueError/TypeError`。

    决策：使用 `defaultdict(str)` 进行安全格式化
    问题：模板可能引用不存在的字段
    方案：缺失键返回空字符串
    代价：字段缺失会静默为空
    重评：需要严格校验时改为显式报错
    """
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
    """按模板将 Data 列表拼接为单个字符串。

    契约：`sep=None` 时回退为换行分隔。
    失败语义：由 `data_to_text_list` 抛出异常向上传递。

    决策：空 `sep` 统一视为换行
    问题：调用方可能传入 `None`
    方案：显式回退到 `\n`
    代价：无法表示“无分隔符”
    重评：若需要空分隔可新增参数
    """
    formatted_text, _ = data_to_text_list(template, data)
    sep = "\n" if sep is None else sep
    return sep.join(formatted_text)


def messages_to_text(template: str, messages: Message | list[Message]) -> str:
    """按模板将 Message 列表转换为文本。

    契约：`messages` 为单个或列表，返回拼接后的文本。
    失败语义：出现非 `Message` 元素时抛 `TypeError`。

    决策：严格校验元素类型
    问题：模板渲染依赖 `Message` 字段结构
    方案：非 Message 直接拒绝
    代价：调用方需要预先转换类型
    重评：若允许自动转换时再放宽校验
    """
    if isinstance(messages, (Message)):
        messages = [messages]
    messages_ = []
    for message in messages:
        if not isinstance(message, Message):
            msg = "All elements in the list must be of type Message."
            raise TypeError(msg)
        messages_.append(message)

    formated_messages = [template.format(data=message.model_dump(), **message.model_dump()) for message in messages_]
    return "\n".join(formated_messages)


def clean_string(s):
    """清理字符串中的空行与多余换行。

    契约：移除空行并将 3+ 连续换行压缩为 2 行。
    失败语义：不抛异常。

    决策：保留双换行作为段落分隔
    问题：过度压缩会损失段落结构
    方案：将 3+ 换行压缩为 2
    代价：无法保留更多空行信息
    重评：若需要保留原格式则关闭清理
    """
    s = re.sub(r"^\s*$", "", s, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", s)


def _serialize_data(data: Data) -> str:
    """将 Data 序列化为 JSON 代码块文本。

    契约：返回包含 Markdown 代码块的 JSON 字符串。
    失败语义：序列化失败抛异常。

    决策：使用 `orjson` 并开启缩进
    问题：调试输出需要可读性
    方案：`OPT_INDENT_2` 美化输出
    代价：字符串体积增大
    重评：当需要更紧凑输出时关闭缩进
    """
    serializable_data = jsonable_encoder(data.data)
    json_bytes = orjson.dumps(serializable_data, option=orjson.OPT_INDENT_2)
    return "```json\n" + json_bytes.decode("utf-8") + "\n```"


def safe_convert(data: Any, *, clean_data: bool = False) -> str:
    """将任意输入安全转换为字符串。

    契约：支持 `str`/`Message`/`Data`/`DataFrame`，其余回退 `str()`。
    关键路径（三步）：
    1) 识别常见类型并走专用格式化
    2) 可选清理 DataFrame 的空行与多余换行
    3) 输出清理后的字符串
    失败语义：转换失败抛 `ValueError`。

    决策：DataFrame 输出采用 Markdown 表格
    问题：需要在聊天/日志中可读展示
    方案：`to_markdown(index=False)`
    代价：大表格会显著膨胀输出
    重评：当输出过大时改为截断或文件落盘
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
                # 注意：清理空行与多余换行，便于表格展示
                data = data.dropna(how="all")
                data = data.replace(r"^\s*$", "", regex=True)
                data = data.replace(r"\n+", "\n", regex=True)

            processed_data = data.replace(r"\|", r"\\|", regex=True)

            return processed_data.to_markdown(index=False)

        return clean_string(str(data))
    except (ValueError, TypeError, AttributeError) as e:
        msg = f"Error converting data: {e!s}"
        raise ValueError(msg) from e


def data_to_dataframe(data: Data | list[Data]) -> DataFrame:
    """将 Data 或 Data 列表转换为 DataFrame。

    契约：单个 `Data` 返回单行 DataFrame。
    失败语义：结构不合法时由 DataFrame 构造抛异常。

    决策：以 `data.data` 作为表格行
    问题：Data 对象可能包含非表格字段
    方案：仅使用 `data` 字段构建
    代价：丢弃 `text` 等其他字段
    重评：若需要保留额外字段再扩展
    """
    if isinstance(data, Data):
        return DataFrame([data.data])
    return DataFrame(data=[d.data for d in data])
