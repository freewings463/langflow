"""
模块名称：输入字段定义

本模块定义各类输入字段模型及其校验逻辑，用于组件参数的结构化描述。
主要功能包括：
- 定义不同输入类型的字段类
- 针对复杂输入执行规范化与校验
- 提供输入类型的动态实例化入口

关键组件：
- 各类 `*Input` 及其校验器
- `instantiate_input`

设计背景：统一表单输入语义，减少组件之间的格式差异。
注意事项：部分输入类型涉及敏感数据，默认不进入遥测。
"""

import warnings
from collections.abc import AsyncIterator, Iterator
from typing import Any, TypeAlias, get_args

from pandas import DataFrame
from pydantic import Field, field_validator, model_validator

from lfx.inputs.validators import CoalesceBool
from lfx.schema.data import Data
from lfx.schema.message import Message

from .input_mixin import (
    AIMixin,
    AuthMixin,
    BaseInputMixin,
    ConnectionMixin,
    DatabaseLoadMixin,
    DropDownMixin,
    FieldTypes,
    FileMixin,
    InputTraceMixin,
    LinkMixin,
    ListableInputMixin,
    MetadataTraceMixin,
    ModelInputMixin,
    MultilineMixin,
    QueryMixin,
    RangeMixin,
    SerializableFieldTypes,
    SliderMixin,
    SortableListMixin,
    TableMixin,
    TabMixin,
    ToolModeMixin,
)


class TableInput(BaseInputMixin, MetadataTraceMixin, TableMixin, ListableInputMixin, ToolModeMixin):
    """表格输入字段。

    契约：
    - 输入：单行/多行数据（dict/Data/DataFrame）
    - 输出：统一为行列表的 value
    - 副作用：无
    - 失败语义：无法转换为列表时抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.TABLE
    is_list: bool = True

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, _info):
        # 注意：单行 dict/Data 统一包装为列表
        if isinstance(v, dict | Data):
            v = [v]
        # 注意：DataFrame 自动转换为行字典列表
        if isinstance(v, DataFrame):
            v = v.to_dict(orient="records")
        # 校验最终结果必须为列表
        if not isinstance(v, list):
            msg = (
                "The table input must be a list of rows. You provided a "
                f"{type(v).__name__}, which cannot be converted to table format. "
                "Please provide your data as either:\n"
                "- A list of dictionaries (each dict is a row)\n"
                "- A pandas DataFrame\n"
                "- A single dictionary (will become a one-row table)\n"
                "- A Data object (Langflow's internal data structure)\n"
            )
            raise ValueError(msg)  # noqa: TRY004
        # 每行只能是 dict 或 Data
        for i, item in enumerate(v):
            if not isinstance(item, dict | Data):
                msg = (
                    f"Row {i + 1} in your table has an invalid format. Each row must be either:\n"
                    "- A dictionary containing column name/value pairs\n"
                    "- A Data object (Langflow's internal data structure for passing data between components)\n"
                    f"Instead, got a {type(item).__name__}. Please check the format of your input data."
                )
                raise ValueError(msg)  # noqa: TRY004
        return v


class HandleInput(BaseInputMixin, ListableInputMixin, MetadataTraceMixin):
    """可连接到特定类型的句柄输入。

    契约：
    - 输入：由上游连接传入的对象
    - 输出：保持原对象
    - 副作用：无
    - 失败语义：由上游类型校验负责
    """

    input_types: list[str] = Field(default_factory=list)
    field_type: SerializableFieldTypes = FieldTypes.OTHER


class ToolsInput(BaseInputMixin, ListableInputMixin, MetadataTraceMixin, ToolModeMixin):
    """工具列表输入。

    契约：
    - 输入：工具配置列表
    - 输出：工具配置列表
    - 副作用：无
    - 失败语义：结构不合法由上层校验抛错
    """

    field_type: SerializableFieldTypes = FieldTypes.TOOLS
    value: list[dict] = Field(default_factory=list)
    is_list: bool = True
    real_time_refresh: bool = True


class DataInput(HandleInput, InputTraceMixin, ListableInputMixin, ToolModeMixin):
    """Data 句柄输入（限定 `Data` 类型）。

    契约：
    - 输入：`Data` 或其列表
    - 输出：保持原对象
    - 副作用：参与输入链路追踪
    - 失败语义：类型不匹配由上层校验抛错
    """

    input_types: list[str] = ["Data"]


class DataFrameInput(HandleInput, InputTraceMixin, ListableInputMixin, ToolModeMixin):
    """DataFrame 句柄输入（限定 `DataFrame` 类型）。

    契约：
    - 输入：`DataFrame` 或其列表
    - 输出：保持原对象
    - 副作用：参与输入链路追踪
    - 失败语义：类型不匹配由上层校验抛错
    """
    input_types: list[str] = ["DataFrame"]


class PromptInput(BaseInputMixin, ListableInputMixin, InputTraceMixin, ToolModeMixin):
    """Prompt 输入字段。

    契约：
    - 输入：文本或模板字符串
    - 输出：文本值
    - 副作用：参与输入链路追踪
    - 失败语义：类型异常由基类校验抛错
    """
    field_type: SerializableFieldTypes = FieldTypes.PROMPT


class MustachePromptInput(PromptInput):
    """Mustache 模板 Prompt 输入字段。

    契约：
    - 输入：Mustache 模板文本
    - 输出：文本值
    - 副作用：继承 Prompt 的追踪语义
    - 失败语义：沿用 Prompt 校验逻辑
    """
    field_type: SerializableFieldTypes = FieldTypes.MUSTACHE_PROMPT


class CodeInput(BaseInputMixin, ListableInputMixin, InputTraceMixin, ToolModeMixin):
    """代码输入字段。

    契约：
    - 输入：代码字符串
    - 输出：代码字符串
    - 副作用：参与输入链路追踪
    - 失败语义：类型异常由基类校验抛错
    """
    field_type: SerializableFieldTypes = FieldTypes.CODE


class ModelInput(BaseInputMixin, ModelInputMixin, ListableInputMixin, InputTraceMixin, ToolModeMixin):
    """模型选择输入（可切换到连接模式）。

    契约：
    - 输入：模型名字符串/列表或字典列表
    - 输出：规范化后的模型选项结构
    - 副作用：必要时启用 `LanguageModel` 连接句柄
    - 失败语义：格式异常时保持原值或由校验器抛错

    说明：
    - 选择 `connect_other_models` 时进入连接模式
    - 字符串/字符串列表会被规范为字典列表
    """

    field_type: SerializableFieldTypes = FieldTypes.MODEL
    placeholder: str | None = "Setup Provider"
    input_types: list[str] = Field(default_factory=list)  # 注意：默认不显示连接句柄
    refresh_button: bool | None = True
    external_options: dict = Field(
        default_factory=lambda: {
            "fields": {
                "data": {
                    "node": {
                        "name": "connect_other_models",
                        "display_name": "Connect other models",
                        "icon": "CornerDownLeft",
                    }
                }
            },
        }
    )

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, v):
        """将模型选择值规范为字典列表或连接模式字符串。

        规则示例：
        - `'gpt-4o'` -> `[{name: 'gpt-4o', ...}]`
        - `['gpt-4o', 'claude-3']` -> `[{name: ...}, ...]`
        - 字典列表保持原样
        - `'connect_other_models'` 保留为字符串以启用连接模式
        """
        # 注意：空值直接返回
        if v is None or v == "":
            return v

        # 注意：连接模式特殊值保持为字符串
        if v == "connect_other_models":
            return v

        # 其他类型直接返回（例如 BaseLanguageModel）
        if not isinstance(v, list | str):
            return v

        # 已为字典列表则原样返回
        if isinstance(v, list) and all(isinstance(item, dict) for item in v):
            return v

        # 字符串或字符串列表 -> 字典列表
        if isinstance(v, str) or (isinstance(v, list) and all(isinstance(item, str) for item in v)):
            # 注意：避免循环依赖，直接导入模块实现
            try:
                from lfx.base.models.unified_models import normalize_model_names_to_dicts

                return normalize_model_names_to_dicts(v)
            except Exception:  # noqa: BLE001
                # 注意：导入失败时回退为基础格式
                if isinstance(v, str):
                    return [{"name": v}]
                return [{"name": item} for item in v]

        # 其他情况保持原样
        return v

    @model_validator(mode="after")
    def set_defaults(self):
        """设置连接模式与默认选项。

        契约：
        - 输入：当前实例状态
        - 输出：更新后的实例
        - 副作用：可能修改 `input_types` 与 `external_options`
        - 失败语义：无
        """
        # 注意：进入连接模式时显示句柄
        if self.value == "connect_other_models" and not self.input_types:
            # 注意：使用 object.__setattr__ 避免校验递归
            object.__setattr__(self, "input_types", ["LanguageModel"])

        # 注意：外部选项缺失时写入默认配置
        if self.external_options is None or len(self.external_options) == 0:
            object.__setattr__(
                self,
                "external_options",
                {
                    "fields": {
                        "data": {
                            "node": {
                                "name": "connect_other_models",
                                "display_name": "Connect other models",
                                "icon": "CornerDownLeft",
                            }
                        }
                    },
                },
            )
        return self


# 注意：为具体输入类型组合 mixin
class StrInput(
    BaseInputMixin,
    ListableInputMixin,
    DatabaseLoadMixin,
    MetadataTraceMixin,
    ToolModeMixin,
):
    """字符串输入字段。

    契约：
    - 输入：字符串或字符串列表
    - 输出：原值（非字符串仅警告）
    - 副作用：可能发出告警日志
    - 失败语义：不抛异常
    """
    field_type: SerializableFieldTypes = FieldTypes.TEXT
    load_from_db: CoalesceBool = False
    """是否允许打开文本编辑器。默认 False。"""

    @staticmethod
    def _validate_value(v: Any, info):
        """校验字符串输入并保留原值。

        契约：
        - 输入：任意类型值
        - 输出：原值（非字符串将触发警告）
        - 副作用：可能发出 `warnings.warn`
        - 失败语义：不抛异常（仅警告）
        """
        if not isinstance(v, str) and v is not None:
            # 注意：当前为警告，后续可升级为错误
            if info.data.get("input_types") and v.__class__.__name__ not in info.data.get("input_types"):
                warnings.warn(
                    f"Invalid value type {type(v)} for input {info.data.get('name')}. "
                    f"Expected types: {info.data.get('input_types')}",
                    stacklevel=4,
                )
            else:
                warnings.warn(
                    f"Invalid value type {type(v)} for input {info.data.get('name')}.",
                    stacklevel=4,
                )
        return v

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, info):
        """校验单值或列表输入并调用 `_validate_value`。"""
        is_list = info.data["is_list"]
        return [cls._validate_value(vv, info) for vv in v] if is_list else cls._validate_value(v, info)


class MessageInput(StrInput, InputTraceMixin):
    """Message 输入字段（支持字符串/迭代器/Message）。

    契约：
    - 输入：`Message`/`dict`/`str`/迭代器
    - 输出：`Message` 实例
    - 副作用：无
    - 失败语义：类型不支持时抛 `ValueError`
    """
    input_types: list[str] = ["Message"]

    @staticmethod
    def _validate_value(v: Any, _info):
        """将输入规范为 `Message` 对象。"""
        # 注意：dict 视为 Message 的序列化结果
        if isinstance(v, dict):
            return Message(**v)
        # 注意：跨模块 Message 兼容转换
        if isinstance(v, Message):
            # 如果来源不同模块则转换为当前类型
            if type(v).__module__ != Message.__module__:
                return Message(**v.model_dump())
            return v
        if isinstance(v, str | AsyncIterator | Iterator):
            return Message(text=v)
        msg = f"Invalid value type {type(v)}"
        raise ValueError(msg)


class MessageTextInput(StrInput, MetadataTraceMixin, InputTraceMixin, ToolModeMixin):
    """文本输入字段（兼容 `Message`/`Data`）。

    契约：
    - 输入：`str`/`Message`/`Data`/迭代器
    - 输出：文本或迭代器
    - 副作用：参与输入追踪与元数据追踪
    - 失败语义：`Data` 缺少 `text_key` 时抛 `ValueError`
    """

    input_types: list[str] = ["Message"]

    @staticmethod
    def _validate_value(v: Any, info):
        """将输入规范为文本或可迭代文本。"""
        value: str | AsyncIterator | Iterator | None = None
        if isinstance(v, dict):
            v = Message(**v)
        if isinstance(v, str):
            value = v
        elif isinstance(v, Message):
            value = v.text
        elif isinstance(v, Data):
            if v.text_key in v.data:
                value = v.data[v.text_key]
            else:
                keys = ", ".join(v.data.keys())
                input_name = info.data["name"]
                msg = (
                    f"The input to '{input_name}' must contain the key '{v.text_key}'."
                    f"You can set `text_key` to one of the following keys: {keys} "
                    "or set the value using another Component."
                )
                raise ValueError(msg)
        elif isinstance(v, AsyncIterator | Iterator):
            value = v
        else:
            msg = f"Invalid value type {type(v)}"
            raise ValueError(msg)  # noqa: TRY004
        return value


class MultilineInput(MessageTextInput, AIMixin, MultilineMixin, InputTraceMixin, ToolModeMixin):
    """多行文本输入字段。

    契约：
    - 输入：文本或 `Message`
    - 输出：文本值
    - 副作用：启用多行与 AI 能力标记
    - 失败语义：沿用 MessageTextInput
    """

    field_type: SerializableFieldTypes = FieldTypes.TEXT
    multiline: CoalesceBool = True
    copy_field: CoalesceBool = False


class MultilineSecretInput(MessageTextInput, MultilineMixin, InputTraceMixin):
    """多行密文输入字段。

    契约：
    - 输入：文本或 `Message`
    - 输出：文本值
    - 副作用：标记为密码字段并禁用遥测
    - 失败语义：沿用 MessageTextInput
    """

    field_type: SerializableFieldTypes = FieldTypes.PASSWORD
    multiline: CoalesceBool = True
    password: CoalesceBool = Field(default=True)
    track_in_telemetry: CoalesceBool = False  # 注意：密文输入不进入遥测


class SecretStrInput(BaseInputMixin, DatabaseLoadMixin):
    """密文字段输入。

    契约：
    - 输入：字符串/`Message`/`Data`/迭代器
    - 输出：字符串或迭代器
    - 副作用：禁用遥测
    - 失败语义：`Data` 缺少 `text_key` 时抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.PASSWORD
    password: CoalesceBool = Field(default=True)
    input_types: list[str] = []
    load_from_db: CoalesceBool = True
    track_in_telemetry: CoalesceBool = False  # 注意：密码不进入遥测

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, info):
        """将输入规范为字符串或可迭代文本。"""
        value: str | AsyncIterator | Iterator | None = None
        if isinstance(v, str):
            value = v
        elif isinstance(v, Message):
            value = v.text
        elif isinstance(v, Data):
            if v.text_key in v.data:
                value = v.data[v.text_key]
            else:
                keys = ", ".join(v.data.keys())
                input_name = info.data["name"]
                msg = (
                    f"The input to '{input_name}' must contain the key '{v.text_key}'."
                    f"You can set `text_key` to one of the following keys: {keys} "
                    "or set the value using another Component."
                )
                raise ValueError(msg)
        elif isinstance(v, AsyncIterator | Iterator):
            value = v
        elif v is None:
            value = None
        else:
            msg = f"Invalid value type `{type(v)}` for input `{info.data['name']}`"
            raise ValueError(msg)
        return value


class IntInput(BaseInputMixin, ListableInputMixin, RangeMixin, MetadataTraceMixin, ToolModeMixin):
    """整数输入字段。

    契约：
    - 输入：整数或可转换的浮点数
    - 输出：整数值
    - 副作用：参与元数据追踪
    - 失败语义：非数值类型抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.INTEGER
    track_in_telemetry: CoalesceBool = True  # 注意：数值参数可进入遥测

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, info):
        """将数值规范为整数。"""
        if v and not isinstance(v, int | float):
            msg = f"Invalid value type {type(v)} for input {info.data.get('name')}."
            raise ValueError(msg)
        if isinstance(v, float):
            v = int(v)
        return v


class FloatInput(BaseInputMixin, ListableInputMixin, RangeMixin, MetadataTraceMixin, ToolModeMixin):
    """浮点数输入字段。

    契约：
    - 输入：整数或浮点数
    - 输出：浮点数值
    - 副作用：参与元数据追踪
    - 失败语义：非数值类型抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.FLOAT
    track_in_telemetry: CoalesceBool = True  # 注意：数值参数可进入遥测

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, info):
        """将数值规范为浮点数。"""
        if v and not isinstance(v, int | float):
            msg = f"Invalid value type {type(v)} for input {info.data.get('name')}."
            raise ValueError(msg)
        if isinstance(v, int):
            v = float(v)
        return v


class BoolInput(BaseInputMixin, ListableInputMixin, MetadataTraceMixin, ToolModeMixin):
    """布尔输入字段。

    契约：
    - 输入：布尔值或可归一化的字符串
    - 输出：`bool`
    - 副作用：参与元数据追踪
    - 失败语义：非法值由 `CoalesceBool` 抛错
    """

    field_type: SerializableFieldTypes = FieldTypes.BOOLEAN
    value: CoalesceBool = False
    track_in_telemetry: CoalesceBool = True  # 注意：布尔标记可进入遥测


class NestedDictInput(
    BaseInputMixin,
    ListableInputMixin,
    MetadataTraceMixin,
    InputTraceMixin,
    ToolModeMixin,
):
    """嵌套字典输入字段。

    契约：
    - 输入：嵌套字典
    - 输出：字典值
    - 副作用：参与输入追踪
    - 失败语义：类型异常由基类校验抛错
    """

    field_type: SerializableFieldTypes = FieldTypes.NESTED_DICT
    value: dict | None = {}


class DictInput(BaseInputMixin, ListableInputMixin, InputTraceMixin, ToolModeMixin):
    """字典输入字段。

    契约：
    - 输入：字典
    - 输出：字典值
    - 副作用：参与输入追踪
    - 失败语义：类型异常由基类校验抛错
    """

    field_type: SerializableFieldTypes = FieldTypes.DICT
    value: dict = Field(default_factory=dict)


class DropdownInput(BaseInputMixin, DropDownMixin, MetadataTraceMixin, ToolModeMixin):
    """下拉选择输入字段。

    契约：
    - 输入：选项值或自定义值
    - 输出：选项值
    - 副作用：可触发对话框或切换逻辑
    - 失败语义：由上层校验决定
    """

    field_type: SerializableFieldTypes = FieldTypes.TEXT
    options: list[str] = Field(default_factory=list)
    options_metadata: list[dict[str, Any]] = Field(default_factory=list)
    combobox: CoalesceBool = False
    dialog_inputs: dict[str, Any] = Field(default_factory=dict)
    external_options: dict[str, Any] = Field(default_factory=dict)
    toggle: bool = False
    toggle_disable: bool | None = None
    toggle_value: bool | None = None
    track_in_telemetry: CoalesceBool = True  # 注意：预置选项可进入遥测


class ConnectionInput(BaseInputMixin, ConnectionMixin, MetadataTraceMixin, ToolModeMixin):
    """连接输入字段（可能含凭据）。

    契约：
    - 输入：连接配置
    - 输出：连接配置
    - 副作用：提供连接入口提示
    - 失败语义：由上层校验决定
    """

    field_type: SerializableFieldTypes = FieldTypes.CONNECTION
    track_in_telemetry: CoalesceBool = False  # 注意：连接信息可能含凭据


class AuthInput(BaseInputMixin, AuthMixin, MetadataTraceMixin):
    """鉴权输入字段。

    契约：
    - 输入：鉴权配置
    - 输出：鉴权配置
    - 副作用：默认隐藏字段
    - 失败语义：由上层校验决定
    """

    field_type: SerializableFieldTypes = FieldTypes.AUTH
    show: bool = False
    track_in_telemetry: CoalesceBool = False  # 注意：鉴权信息不进入遥测


class QueryInput(MessageTextInput, QueryMixin):
    """查询输入字段。

    契约：
    - 输入：查询文本
    - 输出：文本值
    - 副作用：支持分隔符语义
    - 失败语义：沿用 MessageTextInput
    """

    field_type: SerializableFieldTypes = FieldTypes.QUERY
    separator: str | None = Field(default=None)


class SortableListInput(BaseInputMixin, SortableListMixin, MetadataTraceMixin, ToolModeMixin):
    """可排序列表输入字段。

    契约：
    - 输入：选项列表
    - 输出：排序后的列表
    - 副作用：携带选项元数据
    - 失败语义：由上层校验决定
    """

    field_type: SerializableFieldTypes = FieldTypes.SORTABLE_LIST


class TabInput(BaseInputMixin, TabMixin, MetadataTraceMixin, ToolModeMixin):
    """Tab 选择输入字段。

    契约：
    - 输入：Tab 选项值
    - 输出：当前选项值
    - 副作用：限制选项数量与长度
    - 失败语义：值不在选项内时抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.TAB
    options: list[str] = Field(default_factory=list)
    track_in_telemetry: CoalesceBool = True  # 注意：Tab 选择可进入遥测

    @model_validator(mode="after")
    @classmethod
    def validate_value(cls, values):
        """校验当前值是否在选项内。"""
        options = values.options  # 注意：此处已保证 options 可用
        value = values.value

        if not isinstance(value, str):
            msg = f"TabInput value must be a string. Got {type(value).__name__}."
            raise TypeError(msg)

        if value not in options and value != "":
            msg = f"TabInput value must be one of the following: {options}. Got: '{value}'"
            raise ValueError(msg)

        return values


class MultiselectInput(BaseInputMixin, ListableInputMixin, DropDownMixin, MetadataTraceMixin, ToolModeMixin):
    """多选输入字段。

    契约：
    - 输入：字符串列表
    - 输出：字符串列表
    - 副作用：支持多选 UI
    - 失败语义：非列表或含非字符串项时抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.TEXT
    options: list[str] = Field(default_factory=list)
    is_list: bool = Field(default=True, serialization_alias="list")
    combobox: CoalesceBool = False

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any, _info):
        """校验多选值为字符串列表。"""
        if not isinstance(v, list):
            msg = f"MultiselectInput value must be a list. Value: '{v}'"
            raise ValueError(msg)  # noqa: TRY004
        for item in v:
            if not isinstance(item, str):
                msg = f"MultiselectInput value must be a list of strings. Item: '{item}' is not a string"
                raise ValueError(msg)  # noqa: TRY004
        return v


class FileInput(BaseInputMixin, ListableInputMixin, FileMixin, MetadataTraceMixin, ToolModeMixin):
    """文件输入字段。

    契约：
    - 输入：文件路径或路径列表
    - 输出：路径值
    - 副作用：文件类型校验
    - 失败语义：路径/扩展名不合法时抛 `ValueError`
    """

    field_type: SerializableFieldTypes = FieldTypes.FILE
    track_in_telemetry: CoalesceBool = False  # 注意：文件路径可能含 PII


class McpInput(BaseInputMixin, MetadataTraceMixin):
    """MCP 配置输入字段。

    契约：
    - 输入：MCP 配置字典
    - 输出：配置字典
    - 副作用：禁用遥测
    - 失败语义：由上层校验决定
    """

    field_type: SerializableFieldTypes = FieldTypes.MCP
    value: dict[str, Any] = Field(default_factory=dict)
    track_in_telemetry: CoalesceBool = False  # 注意：MCP 配置可能含敏感信息


class LinkInput(BaseInputMixin, LinkMixin):
    """链接输入字段。

    契约：
    - 输入：图标与文本
    - 输出：链接配置
    - 副作用：无
    - 失败语义：由上层校验决定
    """
    field_type: SerializableFieldTypes = FieldTypes.LINK


class SliderInput(BaseInputMixin, RangeMixin, SliderMixin, ToolModeMixin):
    """滑块输入字段。

    契约：
    - 输入：数值范围与步长
    - 输出：数值
    - 副作用：呈现滑块 UI
    - 失败语义：范围校验失败抛 `ValueError`
    """
    field_type: SerializableFieldTypes = FieldTypes.SLIDER


DEFAULT_PROMPT_INTUT_TYPES = ["Message"]

from lfx.template.field.base import Input  # noqa: E402


class DefaultPromptField(Input):
    """默认 Prompt 字段模型（用于组件模板）。

    契约：
    - 输入：文本值
    - 输出：文本值
    - 副作用：无
    - 失败语义：由 `Input` 校验决定
    """
    name: str
    display_name: str | None = None
    field_type: str = "str"
    advanced: bool = False
    multiline: bool = True
    input_types: list[str] = DEFAULT_PROMPT_INTUT_TYPES
    value: Any = ""  # 注意：默认值为空字符串


InputTypes: TypeAlias = (
    Input
    | AuthInput
    | QueryInput
    | DefaultPromptField
    | BoolInput
    | DataInput
    | DictInput
    | DropdownInput
    | MultiselectInput
    | SortableListInput
    | ConnectionInput
    | FileInput
    | FloatInput
    | HandleInput
    | IntInput
    | McpInput
    | ModelInput
    | MultilineInput
    | MultilineSecretInput
    | NestedDictInput
    | ToolsInput
    | PromptInput
    | MustachePromptInput
    | CodeInput
    | SecretStrInput
    | StrInput
    | MessageTextInput
    | MessageInput
    | TableInput
    | LinkInput
    | SliderInput
    | DataFrameInput
    | TabInput
)

InputTypesMap: dict[str, type[InputTypes]] = {t.__name__: t for t in get_args(InputTypes)}


def instantiate_input(input_type: str, data: dict) -> InputTypes:
    """根据类型名实例化输入字段。

    契约：
    - 输入：`input_type` 字符串与字段数据字典
    - 输出：对应输入类型实例
    - 副作用：可能修改 `data` 的字段名
    - 失败语义：未知类型抛 `ValueError`
    """
    input_type_class = InputTypesMap.get(input_type)
    if "type" in data:
        # 注意：兼容旧字段名
        data["field_type"] = data.pop("type")
    if input_type_class:
        return input_type_class(**data)
    msg = f"Invalid input type: {input_type}"
    raise ValueError(msg)
