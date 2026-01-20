"""
模块名称：提示词数据转换工具

本模块提供 `Data`/`Message`/`Document` 到字符串的统一转换，用于提示词模板填充。
主要功能包括：
- `Data` 转文本
- 字典值递归转文本
- `Document` 转文本

关键组件：
- `data_to_string`
- `dict_values_to_string`
- `document_to_string`

设计背景：上游输入类型多样，需要统一字符串化策略。
注意事项：列表中的 `Message`/`Data`/`Document` 会原地转换为字符串。
"""

from copy import deepcopy

from langchain_core.documents import Document

from lfx.schema.data import Data


def data_to_string(record: Data) -> str:
    """将 `Data` 记录转换为字符串

    契约：
    - 输入：`Data` 记录
    - 输出：文本字符串
    - 副作用：无
    - 失败语义：无
    """
    return record.get_text()


def dict_values_to_string(d: dict) -> dict:
    """将字典中的值转换为字符串

    关键路径（三步）：
    1) 深拷贝字典避免改写原对象
    2) 遍历值并按类型转换
    3) 返回字符串化结果

    异常流：无。
    性能瓶颈：嵌套列表较大时。
    排障入口：无。
    
    契约：
    - 输入：字典
    - 输出：值已字符串化的字典
    - 副作用：无
    - 失败语义：无
    """
    from lfx.schema.message import Message

    # 注意：使用深拷贝避免修改原始输入
    d_copy = deepcopy(d)
    for key, value in d_copy.items():
        # 注意：值可能是 `Data`/`Document`/`Message` 列表
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, Message):
                    d_copy[key][i] = item.text
                elif isinstance(item, Data):
                    d_copy[key][i] = data_to_string(item)
                elif isinstance(item, Document):
                    d_copy[key][i] = document_to_string(item)
        elif isinstance(value, Message):
            d_copy[key] = value.text
        elif isinstance(value, Data):
            d_copy[key] = data_to_string(value)
        elif isinstance(value, Document):
            d_copy[key] = document_to_string(value)
    return d_copy


def document_to_string(document: Document) -> str:
    """将 `Document` 转换为字符串

    契约：
    - 输入：`Document` 文档对象
    - 输出：文本字符串
    - 副作用：无
    - 失败语义：无
    """
    return document.page_content
