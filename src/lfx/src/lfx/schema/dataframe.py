"""DataFrame 扩展类型。

本模块基于 pandas.DataFrame 提供与 Data/Message 的互转能力。
"""

from typing import TYPE_CHECKING, cast

import pandas as pd
from langchain_core.documents import Document
from pandas import DataFrame as pandas_DataFrame

from lfx.schema.data import Data

if TYPE_CHECKING:
    from lfx.schema.message import Message


class DataFrame(pandas_DataFrame):
    """面向 Data 对象的 DataFrame 扩展。

    关键路径（三步）：
    1) 初始化并规范化输入数据；
    2) 提供与 Data/Document 的互转；
    3) 支持追加行与批量追加。
    """

    def __init__(
        self,
        data: list[dict] | list[Data] | pd.DataFrame | None = None,
        text_key: str = "text",
        default_value: str = "",
        **kwargs,
    ):
        # 先初始化空 DataFrame
        super().__init__(**kwargs)  # 已移除 data 参数

        # 使用私有属性避免与 pandas 字段冲突
        self._text_key = text_key
        self._default_value = default_value

        if data is None:
            return

        if isinstance(data, list):
            if all(isinstance(x, Data) for x in data):
                data = [d.data for d in data if hasattr(d, "data")]
            elif not all(isinstance(x, dict) for x in data):
                msg = "List items must be either all Data objects or all dictionaries"
                raise ValueError(msg)
            self._update(data, **kwargs)
        elif isinstance(data, dict | pd.DataFrame):  # 修正类型判断
            self._update(data, **kwargs)

    def _update(self, data, **kwargs):
        """Helper method to update DataFrame with new data.

        契约：
        - 输入：新数据和额外参数
        - 输出：无（原地更新）
        - 副作用：修改 DataFrame 内容
        - 失败语义：数据格式错误时抛出异常
        """
        new_df = pd.DataFrame(data, **kwargs)
        self._update_inplace(new_df)

    # 属性访问器
    @property
    def text_key(self) -> str:
        """获取文本键。

        契约：
        - 输入：无
        - 输出：当前文本键
        - 副作用：无
        - 失败语义：无
        """
        return self._text_key

    @text_key.setter
    def text_key(self, value: str) -> None:
        """设置文本键。

        契约：
        - 输入：新的文本键
        - 输出：无
        - 副作用：修改内部文本键
        - 失败语义：文本键不在列中时抛出 ValueError
        """
        if value not in self.columns:
            msg = f"Text key '{value}' not found in DataFrame columns"
            raise ValueError(msg)
        self._text_key = value

    @property
    def default_value(self) -> str:
        """获取默认值。

        契约：
        - 输入：无
        - 输出：当前默认值
        - 副作用：无
        - 失败语义：无
        """
        return self._default_value

    @default_value.setter
    def default_value(self, value: str) -> None:
        """设置默认值。

        契约：
        - 输入：新的默认值
        - 输出：无
        - 副作用：修改内部默认值
        - 失败语义：无
        """
        self._default_value = value

    def to_data_list(self) -> list[Data]:
        """转换为 Data 列表。"""
        list_of_dicts = self.to_dict(orient="records")
        # 可选写法：Data(**row)
        return [Data(data=row) for row in list_of_dicts]

    def add_row(self, data: dict | Data) -> "DataFrame":
        """追加单行数据。"""
        if isinstance(data, Data):
            data = data.data
        new_df = self._constructor([data])
        return cast("DataFrame", pd.concat([self, new_df], ignore_index=True))

    def add_rows(self, data: list[dict | Data]) -> "DataFrame":
        """追加多行数据。"""
        processed_data = []
        for item in data:
            if isinstance(item, Data):
                processed_data.append(item.data)
            else:
                processed_data.append(item)
        new_df = self._constructor(processed_data)
        return cast("DataFrame", pd.concat([self, new_df], ignore_index=True))

    @property
    def _constructor(self):
        """返回 DataFrame 构造函数。

        契约：
        - 输入：无
        - 输出：DataFrame 构造函数
        - 副作用：无
        - 失败语义：无
        """
        def _c(*args, **kwargs):
            return DataFrame(*args, **kwargs).__finalize__(self)

        return _c

    def __bool__(self):
        """返回 DataFrame 是否非空。"""
        return not self.empty

    __hash__ = None  # DataFrame 可变，不可哈希

    def to_lc_documents(self) -> list[Document]:
        """转换为 LangChain Document 列表。"""
        list_of_dicts = self.to_dict(orient="records")
        documents = []
        for row in list_of_dicts:
            data_copy = row.copy()
            text = data_copy.pop(self._text_key, self._default_value)
            if isinstance(text, str):
                documents.append(Document(page_content=text, metadata=data_copy))
            else:
                documents.append(Document(page_content=str(text), metadata=data_copy))
        return documents

    def _docs_to_dataframe(self, docs):
        """将 Document 列表转换为 DataFrame。"""
        return DataFrame(docs)

    def __eq__(self, other):
        """自定义相等比较，规避空表与非表对象误判。"""
        if self.empty:
            return False
        if isinstance(other, list) and not other:  # 空列表
            return False
        if not isinstance(other, DataFrame | pd.DataFrame):  # 非 DataFrame
            return False
        return super().__eq__(other)

    def to_data(self) -> Data:
        """转换为 Data（results 列表）。"""
        dict_list = self.to_dict(orient="records")
        return Data(data={"results": dict_list})

    def to_message(self) -> "Message":
        """转换为 Markdown 文本的 Message。"""
        from lfx.schema.message import Message

        # 按 safe_convert 逻辑处理
        # 移除空行
        processed_df = self.dropna(how="all")
        # 移除单元格空行
        processed_df = processed_df.replace(r"^\s*$", "", regex=True)
        # 多个换行压缩为一个
        processed_df = processed_df.replace(r"\n+", "\n", regex=True)
        # 转义管道符避免 Markdown 表格错位
        processed_df = processed_df.replace(r"\|", r"\\|", regex=True)
        processed_df = processed_df.map(lambda x: str(x).replace("\n", "<br/>") if isinstance(x, str) else x)
        # 转为 Markdown 并封装 Message
        return Message(text=processed_df.to_markdown(index=False))
