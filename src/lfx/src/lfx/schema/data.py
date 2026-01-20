"""模块名称：轻量 Data 结构（lfx）

本模块提供不依赖 langflow 的数据载体，用于在文本、元数据、文档与消息之间转换。主要功能包括：
- 文本字段统一读写（`text_key`/`default_value`）
- 与 LangChain `Document`/`BaseMessage` 的互转
- 数据合并、过滤与 JSON 序列化

关键组件：
- Data：核心数据模型，支持跨模块实例兼容
- custom_serializer/serialize_data：统一序列化策略

设计背景：lfx 需要在脱离 langflow 依赖的场景下复用数据结构与转换能力。
注意事项：`data` 为可变字典，部分方法会就地修改。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from uuid import UUID

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, model_serializer, model_validator

from lfx.log.logger import logger
from lfx.schema.cross_module import CrossModuleModel
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_USER
from lfx.utils.image import create_image_content_dict

if TYPE_CHECKING:
    from lfx.schema.dataframe import DataFrame
    from lfx.schema.message import Message


class Data(CrossModuleModel):
    """轻量数据载体，封装文本与扩展字段。

    契约：`data` 必须为 dict；`text_key` 指向文本字段；`default_value` 用于缺省文本。
    副作用：`set_text`/属性代理会就地修改 `data`。
    失败语义：初始化非 dict 抛 ValueError；消息转换缺键抛 ValueError。
    关键路径：`validate_data` 规范化输入；转换方法提供互操作。
    决策：使用 `data` dict 作为唯一存储。
    问题：需要轻量、可扩展且脱离 langflow 的记录结构。
    方案：`data` + `text_key`，并提供属性式读写与互转方法。
    代价：缺少强类型约束，键冲突在运行期暴露。
    重评：当类型安全或权限边界成为主要风险时。
    """

    model_config = ConfigDict(validate_assignment=True)

    text_key: str = "text"
    data: dict = {}
    default_value: str | None = ""

    @model_validator(mode="before")
    @classmethod
    def validate_data(cls, values):
        """规范化输入数据并填充缺省字段。

        契约：输入 `values` 字典；输出规范化后的 dict；副作用无。
        关键路径（三步）：
        1) 校验 `values` 类型
        2) 确保 `data` 为 dict
        3) 合并非标准键
        失败语义：`values` 不是 dict 时抛 ValueError；`data` 非 dict 仅告警。
        排障入口：`logger.warning` 输出 `Invalid data format`。
        决策：将非标准键并入 `data` 以保留字段。
        问题：调用方常直接传入额外键，易丢失信息。
        方案：保留 `text_key/data/default_value`，其余键合并入 `data`。
        代价：可能引入与 `data` 既有键冲突。
        重评：当字段冲突频发或需要强类型约束时。
        """
        if not isinstance(values, dict):
            msg = "Data must be a dictionary"
            raise ValueError(msg)  # noqa: TRY004
        if "data" not in values or values["data"] is None:
            values["data"] = {}
        if not isinstance(values["data"], dict):
            msg = (
                f"Invalid data format: expected dictionary but got {type(values).__name__}."
                " This will raise an error in version langflow==1.3.0."
            )
            # 排障：输出数据格式警告
            logger.warning(msg)
        # 注意：非标准键统一并入 data，避免字段丢失。
        for key in values:
            if key not in values["data"] and key not in {"text_key", "data", "default_value"}:
                values["data"][key] = values[key]
        return values

    @model_serializer(mode="plain", when_used="json")
    def serialize_model(self):
        """按 JSON 语义序列化 `data`。

        契约：输出 dict；副作用：若字段对象实现 `to_json` 则被调用。
        失败语义：`to_json` 抛出的异常向上冒泡。
        决策：优先使用对象自带 `to_json` 以保持字段自定义格式。
        问题：嵌套对象可能需要自定义 JSON 表达。
        方案：存在 `to_json` 时调用，否则原样返回。
        代价：`to_json` 开销不可控。
        重评：当序列化性能成为瓶颈时。
        """
        return {k: v.to_json() if hasattr(v, "to_json") else v for k, v in self.data.items()}

    def get_text(self):
        """获取文本字段值。

        契约：输出 `data[text_key]` 或 `default_value`；副作用无。
        失败语义：无（缺键时返回默认值）。
        决策：缺键回退 `default_value` 而非抛异常。
        问题：部分上游仅传递元数据无文本。
        方案：读取缺省值以保证调用方无需分支。
        代价：可能掩盖缺失文本的错误配置。
        重评：当缺失文本需要强制报错时。
        """
        return self.data.get(self.text_key, self.default_value)

    def set_text(self, text: str | None) -> str:
        r"""在数据字典中设置文本值。

        契约：输入 str|None；输出规范化后的 str；副作用：写入 `data[text_key]`。
        失败语义：无（使用 `str` 转换）。
        决策：将 None 转为空字符串并强制 `str`。
        问题：上游可能传入 None 或非字符串。
        方案：统一转换为 `str`，避免序列化异常。
        代价：丢失原始类型信息。
        重评：当需要保留原始类型时。
        """
        new_text = "" if text is None else str(text)
        self.data[self.text_key] = new_text
        return new_text

    @classmethod
    def from_document(cls, document: Document) -> Data:
        """由 LangChain `Document` 构造 Data。

        契约：输入 Document；输出 Data；副作用：在 metadata 中写入 `text`。
        失败语义：无显式异常；依赖 Document 字段完整性。
        关键路径：提取 metadata → 写入 page_content → 构造 Data。
        决策：使用 `text` 固定键保存正文。
        问题：下游需要统一文本入口。
        方案：将 `page_content` 写入 `data['text']`。
        代价：与已有 `text` 键冲突时覆盖。
        重评：当上游需要保留原 `text` 字段时。
        """
        data = document.metadata
        data["text"] = document.page_content
        return cls(data=data, text_key="text")

    @classmethod
    def from_lc_message(cls, message: BaseMessage) -> Data:
        """由 LangChain `BaseMessage` 构造 Data。

        契约：输入 BaseMessage；输出 Data；副作用：写入 `metadata` 与 `text`。
        失败语义：`message.to_json()` 抛出的异常向上冒泡。
        关键路径：读取 content → 序列化消息 → 构造 Data。
        决策：将序列化结果放入 `metadata`。
        问题：需要保留消息结构以便回放/排障。
        方案：`metadata=message.to_json()`，`text=content`。
        代价：metadata 体积增加。
        重评：当存储体积成为瓶颈时。
        """
        data: dict = {"text": message.content}
        data["metadata"] = cast("dict", message.to_json())
        return cls(data=data, text_key="text")

    def __add__(self, other: Data) -> Data:
        """合并两个 Data 的 `data` 字典。

        契约：输入另一 Data；输出新 Data；副作用：不修改原对象。
        关键路径（三步）：
        1) 复制当前 data
        2) 遍历 other.data
        3) 可加则相加，否则回退覆盖
        失败语义：仅捕获 `TypeError`，其他异常向上冒泡。
        排障入口：无日志；调用方可在异常时重试/降级。
        决策：同键值优先尝试加法。
        问题：部分字段（如数值/列表）需要累积。
        方案：支持 `+=`，失败则使用 other 值。
        代价：类型不兼容时会丢失原值。
        重评：当需要更细粒度的合并策略时。
        """
        combined_data = self.data.copy()
        for key, value in other.data.items():
            if key in combined_data:
                # 注意：仅在同键且支持 `+=` 时合并，失败回退覆盖。
                try:
                    combined_data[key] += value
                except TypeError:
                    combined_data[key] = value
            else:
                combined_data[key] = value

        return Data(data=combined_data)

    def to_lc_document(self) -> Document:
        """转换为 LangChain `Document`。

        契约：输出 Document；副作用：无（复制 data）。
        失败语义：无；非字符串文本会被 `str` 化。
        关键路径：复制 data → 提取 `text_key` → 构造 Document。
        决策：非字符串文本强制 `str` 以保证兼容。
        问题：Document 要求 `page_content` 为字符串。
        方案：`str(text)` 回退。
        代价：可能丢失原始类型。
        重评：当需要保留结构化文本时。
        """
        data_copy = self.data.copy()
        text = data_copy.pop(self.text_key, self.default_value)
        if isinstance(text, str):
            return Document(page_content=text, metadata=data_copy)
        return Document(page_content=str(text), metadata=data_copy)

    def to_lc_message(
        self,
    ) -> BaseMessage:
        """转换为 LangChain `BaseMessage`。

        契约：要求 `data` 包含 `text` 与 `sender`；输出 AIMessage 或 HumanMessage。
        副作用：读取 `files` 生成多模态内容；未修改 `data`。
        失败语义：缺少必需键抛 ValueError；文件路径解析失败向上冒泡。
        关键路径（三步）：
        1) 校验必需键
        2) 按 sender 构造消息（含 files）
        3) 返回消息实例
        排障入口：异常信息包含缺失键与 data 快照。
        决策：仅在 `sender==user` 时构造多模态内容。
        问题：AI 回复不需要 files，而用户消息可能携带图片。
        方案：用户分支解析文件并插入文本内容。
        代价：files 解析依赖文件系统与格式。
        重评：当多模态协议变化或 sender 类型扩展时。
        """
        # 注意：强制要求 text/sender 同时存在，避免隐式默认导致语义偏差。
        if not all(key in self.data for key in ["text", "sender"]):
            msg = f"Missing required keys ('text', 'sender') in Data: {self.data}"
            raise ValueError(msg)
        sender = self.data.get("sender", MESSAGE_SENDER_AI)
        text = self.data.get("text", "")
        files = self.data.get("files", [])
        if sender == MESSAGE_SENDER_USER:
            if files:
                from lfx.schema.image import get_file_paths

                resolved_file_paths = get_file_paths(files)
                contents = [create_image_content_dict(file_path) for file_path in resolved_file_paths]
                # 实现：文本放在首位，符合多模态内容顺序要求。
                contents.insert(0, {"type": "text", "text": text})
                human_message = HumanMessage(content=contents)
            else:
                human_message = HumanMessage(
                    content=[{"type": "text", "text": text}],
                )

            return human_message

        return AIMessage(content=text)

    def __getattr__(self, key):
        """将未知属性映射到 `data` 字典。

        契约：普通属性读取 `data[key]`；副作用无。
        失败语义：缺失键抛 AttributeError。
        决策：保留 dunder/私有属性走默认路径。
        问题：需要属性式访问而不破坏内部字段。
        方案：过滤 `__*`/`_*`/核心字段后访问 data。
        代价：键名与属性名冲突时行为不直观。
        重评：当需要更严格的属性白名单时。
        """
        try:
            if key.startswith("__"):
                return self.__getattribute__(key)
            if key in {"data", "text_key"} or key.startswith("_"):
                return super().__getattr__(key)
            return self.data[key]
        except KeyError as e:
            msg = f"'{type(self).__name__}' object has no attribute '{key}'"
            raise AttributeError(msg) from e

    def __setattr__(self, key, value) -> None:
        """将未知属性写入 `data` 字典或模型字段。

        契约：模型字段与核心字段走对象属性；其他写入 `data`。
        副作用：写入 `data`，可能覆盖同名键。
        失败语义：无；遵循 Pydantic 校验规则。
        决策：模型字段同步写入 `data` 与属性。
        问题：保持 data 与 Pydantic 字段一致性。
        方案：命中 model_fields 时双写。
        代价：双写增加一次赋值成本。
        重评：当字段数量很大导致性能问题时。
        """
        if key in {"data", "text_key"} or key.startswith("_"):
            super().__setattr__(key, value)
        elif key in type(self).model_fields:
            self.data[key] = value
            super().__setattr__(key, value)
        else:
            self.data[key] = value

    def __delattr__(self, key) -> None:
        """删除 `data` 中的键或对象属性。

        契约：核心字段走默认删除；其他删除 `data` 键。
        失败语义：`del self.data[key]` 可能抛 KeyError。
        决策：私有字段不映射到 data。
        问题：避免删除内部状态。
        方案：过滤核心字段后再删 data。
        代价：键不存在时抛 KeyError。
        重评：当需要静默删除时。
        """
        if key in {"data", "text_key"} or key.startswith("_"):
            super().__delattr__(key)
        else:
            del self.data[key]

    def __deepcopy__(self, memo):
        """深拷贝 Data。

        契约：返回新 Data；副作用无。
        失败语义：copy.deepcopy 传播异常。
        决策：仅复制 `data`，复用 `text_key/default_value`。
        问题：需要可复制且保持配置一致。
        方案：深拷贝 data，并传入配置字段。
        代价：大数据结构复制成本高。
        重评：当 data 非深拷贝可接受时。
        """
        return Data(data=copy.deepcopy(self.data, memo), text_key=self.text_key, default_value=self.default_value)

    def __dir__(self):
        """将 `data` 的键暴露为可补全属性。"""
        return super().__dir__() + list(self.data.keys())

    def __str__(self) -> str:
        """返回 JSON 字符串表示。

        契约：输出 JSON 字符串；副作用：调用字段 `to_json`。
        失败语义：序列化失败时返回 `str(self.data)` 并记录 debug。
        排障入口：`logger.debug("Error converting Data to JSON")`。
        决策：失败时降级为原始字符串而非抛异常。
        问题：字符串化用于日志/调试，不能中断主流程。
        方案：捕获异常并回退。
        代价：可能隐藏序列化错误。
        重评：当调用方需要强失败时。
        """
        try:
            data = {k: v.to_json() if hasattr(v, "to_json") else v for k, v in self.data.items()}
            return serialize_data(data)
        except Exception:  # noqa: BLE001
            logger.debug("Error converting Data to JSON", exc_info=True)
            return str(self.data)

    def __contains__(self, key) -> bool:
        return key in self.data

    def __eq__(self, /, other):
        return isinstance(other, Data) and self.data == other.data

    def filter_data(self, filter_str: str) -> Data:
        """按 JSON 过滤表达式筛选数据。

        契约：输入过滤字符串；输出新 Data；副作用无。
        失败语义：由 `apply_json_filter` 决定（可能抛异常）。
        关键路径：调用模板过滤器并返回结果。
        决策：复用模板层的过滤实现以保持一致语法。
        问题：过滤规则与模板渲染需一致。
        方案：委托 `apply_json_filter`。
        代价：错误定位依赖模板层实现。
        重评：当过滤语法需要解耦时。
        """
        from lfx.template.utils import apply_json_filter

        return apply_json_filter(self.data, filter_str)

    def to_message(self) -> Message:
        """转换为内部 Message 模型。

        契约：输出 Message；副作用无。
        失败语义：无显式异常；缺少 text_key 时使用 data 字符串。
        决策：当缺少文本键时以 `str(data)` 兜底。
        问题：部分数据仅为结构化元数据。
        方案：兜底字符串以保证 Message 可构造。
        代价：信息表达不够结构化。
        重评：当 Message 需要结构化 payload 时。
        """
        from lfx.schema.message import Message  # 本地导入以避免循环导入

        if self.text_key in self.data:
            return Message(text=self.get_text())
        return Message(text=str(self.data))

    def to_dataframe(self) -> DataFrame:
        """转换为 DataFrame。

        契约：输出 DataFrame；副作用无。
        失败语义：由 DataFrame 构造器决定。
        关键路径：检测单键列表结构 → 直接作为行；否则包装为单行。
        决策：单键且值为 dict 列表时视为行集合。
        问题：上游常传入 `{key: [row,...]}` 结构。
        方案：识别该形态并直接展开。
        代价：键名被忽略。
        重评：当需要保留键名作为列名时。
        """
        from lfx.schema.dataframe import DataFrame  # 本地导入以避免循环导入

        data_dict = self.data
        # 注意：单键 dict + 列表结构直接视作表格行集合。
        if (
            len(data_dict) == 1
            and isinstance(next(iter(data_dict.values())), list)
            and all(isinstance(item, dict) for item in next(iter(data_dict.values())))
        ):
            return DataFrame(data=next(iter(data_dict.values())))
        return DataFrame(data=[self])

    def __repr__(self) -> str:
        """返回可重建的调试表示。"""
        return f"Data(text_key={self.text_key!r}, data={self.data!r}, default_value={self.default_value!r})"

    def __hash__(self) -> int:
        """基于 repr 生成哈希。"""
        return hash(self.__repr__())


def custom_serializer(obj):
    """自定义 JSON 序列化规则集合。

    契约：输入任意对象；输出可序列化值；副作用无。
    失败语义：不支持类型抛 TypeError。
    决策：统一转 UTC 字符串与基本类型。
    问题：json.dumps 无法处理 datetime/Decimal/UUID 等。
    方案：按类型映射到 JSON 友好值。
    代价：精度或时区信息可能丢失。
    重评：当需要保留原始精度/时区时。
    """
    if isinstance(obj, datetime):
        utc_date = obj.replace(tzinfo=timezone.utc)
        return utc_date.strftime("%Y-%m-%d %H:%M:%S %Z")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    msg = f"Type {type(obj)} not serializable"
    raise TypeError(msg)


def serialize_data(data):
    """将数据序列化为 JSON 字符串。

    契约：输入任意数据；输出 JSON 字符串；副作用：使用 custom_serializer。
    失败语义：custom_serializer 抛 TypeError 向上冒泡。
    决策：启用缩进 4 以便调试可读。
    问题：需要人类可读的输出用于日志/排障。
    方案：`json.dumps(..., indent=4, default=custom_serializer)`。
    代价：输出体积更大。
    重评：当性能或带宽成为主要成本时。
    """
    return json.dumps(data, indent=4, default=custom_serializer)
