"""
模块名称：输入字段混入模型

本模块定义输入字段的通用属性与混入类，用于组合出不同类型的输入模型。
主要功能包括：
- 统一字段类型枚举与序列化规则
- 提供文件、下拉、范围、模型等常用混入
- 对关键字段执行 Pydantic 校验

关键组件：
- `BaseInputMixin`
- `FieldTypes`
- 各类 *Mixin

设计背景：通过 mixin 组合减少重复字段定义并保持一致校验语义。
注意事项：部分字段涉及敏感信息，默认不进入遥测。
"""

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_serializer,
)

from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.validators import CoalesceBool
from lfx.schema.cross_module import CrossModuleModel


class FieldTypes(str, Enum):
    """输入字段类型枚举。"""
    TEXT = "str"
    INTEGER = "int"
    PASSWORD = "str"  # noqa: PIE796 pragma: allowlist secret
    FLOAT = "float"
    BOOLEAN = "bool"
    DICT = "dict"
    NESTED_DICT = "NestedDict"
    SORTABLE_LIST = "sortableList"
    CONNECTION = "connect"
    AUTH = "auth"
    FILE = "file"
    PROMPT = "prompt"
    MUSTACHE_PROMPT = "mustache"
    CODE = "code"
    OTHER = "other"
    TABLE = "table"
    LINK = "link"
    SLIDER = "slider"
    TAB = "tab"
    QUERY = "query"
    TOOLS = "tools"
    MCP = "mcp"
    MODEL = "model"


SerializableFieldTypes = Annotated[FieldTypes, PlainSerializer(lambda v: v.value, return_type=str)]

# 注意：敏感字段类型不进入遥测
SENSITIVE_FIELD_TYPES = {
    FieldTypes.PASSWORD,
    FieldTypes.AUTH,
    FieldTypes.FILE,
    FieldTypes.CONNECTION,
    FieldTypes.MCP,
}


# 输入字段通用混入
class BaseInputMixin(CrossModuleModel, validate_assignment=True):  # type: ignore[call-arg]
    """输入字段通用混入。

    契约：
    - 输入：字段配置与默认值
    - 输出：可序列化的输入字段模型
    - 副作用：序列化时写入 `_input_type`
    - 失败语义：字段校验失败抛 `ValueError`
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        populate_by_name=True,
    )

    field_type: SerializableFieldTypes = Field(default=FieldTypes.TEXT, alias="type")

    override_skip: bool = False
    """字段是否强制保留（不允许被跳过）。默认 False。"""

    required: bool = False
    """字段是否必填。默认 False。"""

    placeholder: str = ""
    """输入占位文本。默认空字符串。"""

    show: bool = True
    """字段是否显示。默认 True。"""

    name: str = Field(description="Name of the field.")
    """字段名。默认空字符串。"""

    value: Any = ""
    """字段值。默认空字符串。"""

    display_name: str | None = None
    """展示名。默认 None。"""

    advanced: bool = False
    """是否为高级参数（通常隐藏）。默认 False。"""

    input_types: list[str] | None = None
    """多类型输入时的可连接类型列表。默认空列表。"""

    dynamic: bool = False
    """字段是否动态。默认 False。"""

    helper_text: str | None = None
    """字段辅助说明文本。默认空字符串。"""

    info: str | None = ""
    """用于提示框的补充说明。默认空字符串。"""

    real_time_refresh: bool | None = None
    """是否启用实时刷新（与 `refresh_button` 互斥）。默认 None。"""

    refresh_button: bool | None = None
    """是否显示刷新按钮。默认 False。"""

    refresh_button_text: str | None = None
    """刷新按钮文本。默认 None。"""

    title_case: bool = False
    """是否以标题格式显示。默认 False。"""

    track_in_telemetry: CoalesceBool = False
    """是否允许进入遥测。

    默认 False（需显式开启）。敏感字段将强制关闭。
    """

    def to_dict(self):
        """输出可序列化字典，忽略空值字段。"""
        return self.model_dump(exclude_none=True, by_alias=True)

    @field_validator("field_type", mode="before")
    @classmethod
    def validate_field_type(cls, v):
        """将输入值规范化为 `FieldTypes`，无法识别时回退为 `OTHER`。"""
        try:
            return FieldTypes(v)
        except ValueError:
            return FieldTypes.OTHER

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        """序列化时输出 `type` 字段并标记 `_input_type`。"""
        dump = handler(self)
        if "field_type" in dump:
            dump["type"] = dump.pop("field_type")
        dump["_input_type"] = self.__class__.__name__
        return dump


class ModelInputMixin(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """模型输入混入。"""
    model_name: str | None = None
    """模型名称。"""
    model_type: str | None = "language"
    """模型类型：`language` 或 `embedding`。默认 `language`。"""
    model_options: list[dict[str, Any]] | None = Field(
        default=None,
        validation_alias="options",
        serialization_alias="options",
    )
    """模型选项列表（包含 name/icon/category/provider/metadata）。"""
    temperature: float | None = None
    """生成温度参数。"""
    max_tokens: int | None = None
    """最大生成 token 数。"""
    limit: int | None = None
    """下拉展示上限。"""
    external_options: dict[str, Any] | None = None
    """下拉之外的扩展选项配置（如“连接其他模型”）。"""

    @field_validator("model_options", mode="before")
    @classmethod
    def normalize_model_options(cls, v):
        """将模型名列表规范为字典列表。

        允许传入：
        - `['gpt-4o', 'gpt-4o-mini']` -> `[{name: ...}, ...]`
        - 已是字典列表则原样返回
        """
        if v is None or not isinstance(v, list):
            return v

        # 已为字典列表则原样返回
        if all(isinstance(item, dict) for item in v):
            return v

        # 字符串列表 -> 字典列表
        if all(isinstance(item, str) for item in v):
            # 注意：避免循环依赖，直接导入模块实现
            try:
                from lfx.base.models.unified_models import normalize_model_names_to_dicts

                return normalize_model_names_to_dicts(v)
            except Exception:  # noqa: BLE001
                # 注意：导入失败时回退为基础格式
                return [{"name": item} for item in v]

        # 混合列表或不规范格式保持原样
        return v


class ToolModeMixin(BaseModel):
    """工具模式标记混入。

    契约：
    - 输入：`tool_mode` 布尔值
    - 输出：字段值
    - 副作用：无
    - 失败语义：无
    """
    tool_mode: bool = False


class InputTraceMixin(BaseModel):
    """输入追踪标记混入。

    契约：
    - 输入：`trace_as_input` 布尔值
    - 输出：字段值
    - 副作用：参与链路追踪
    - 失败语义：无
    """
    trace_as_input: bool = True


class MetadataTraceMixin(BaseModel):
    """元数据追踪标记混入。

    契约：
    - 输入：`trace_as_metadata` 布尔值
    - 输出：字段值
    - 副作用：参与元数据追踪
    - 失败语义：无
    """
    trace_as_metadata: bool = True


# 可列表化字段混入
class ListableInputMixin(BaseModel):
    """可列表化字段混入。

    契约：
    - 输入：`is_list` 与 `list_add_label`
    - 输出：列表化配置
    - 副作用：影响表单展示
    - 失败语义：无
    """
    is_list: bool = Field(default=False, alias="list")
    list_add_label: str | None = Field(default="Add More")


# 需要数据库交互的字段混入
class DatabaseLoadMixin(BaseModel):
    """数据库加载标记混入。

    契约：
    - 输入：`load_from_db` 布尔值
    - 输出：字段值
    - 副作用：提示前端加载 DB 变量
    - 失败语义：无
    """
    load_from_db: bool = Field(default=True)


class AuthMixin(BaseModel):
    """鉴权提示混入。

    契约：
    - 输入：`auth_tooltip` 文本
    - 输出：提示文本
    - 副作用：无
    - 失败语义：无
    """
    auth_tooltip: str | None = Field(default="")


class QueryMixin(BaseModel):
    """查询分隔符混入。

    契约：
    - 输入：`separator` 字符
    - 输出：分隔符配置
    - 副作用：影响查询拆分逻辑
    - 失败语义：无
    """
    separator: str | None = Field(default=None)
    """查询分隔符。默认 None。"""


# 需要文件交互的字段混入
class FileMixin(BaseModel):
    """文件字段混入。

    契约：
    - 输入：`file_path` 与 `file_types`
    - 输出：文件配置
    - 副作用：触发路径/类型校验
    - 失败语义：非法类型抛 `ValueError`
    """
    file_path: list[str] | str | None = Field(default="")
    file_types: list[str] = Field(default=[], alias="fileTypes")
    temp_file: bool = Field(default=False)

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v):
        """校验文件路径类型（字符串或字符串列表）。"""
        if v is None or v == "":
            return v
        # 注意：列表场景逐项校验类型
        if isinstance(v, list):
            for item in v:
                if not isinstance(item, str):
                    msg = "All file paths must be strings"
                    raise TypeError(msg)
            return v
        # 单字符串路径合法
        if isinstance(v, str):
            return v
        msg = "file_path must be a string, list of strings, or None"
        raise ValueError(msg)

    @field_validator("file_types")
    @classmethod
    def validate_file_types(cls, v):
        """校验文件类型列表（不允许包含点前缀）。"""
        if not isinstance(v, list):
            msg = "file_types must be a list"
            raise ValueError(msg)  # noqa: TRY004
        # 注意：扩展名不包含点号
        for file_type in v:
            if not isinstance(file_type, str):
                msg = "file_types must be a list of strings"
                raise ValueError(msg)  # noqa: TRY004
            if file_type.startswith("."):
                msg = "file_types should not start with a dot"
                raise ValueError(msg)
        return v


class RangeMixin(BaseModel):
    """范围参数混入。

    契约：
    - 输入：`range_spec` 或其字典
    - 输出：`RangeSpec` 实例
    - 副作用：触发转换校验
    - 失败语义：类型不匹配抛 `ValueError`
    """
    range_spec: RangeSpec | None = None

    @field_validator("range_spec", mode="before")
    @classmethod
    def validate_range_spec(cls, v):
        """支持 dict 到 RangeSpec 的转换。"""
        if v is None:
            return v
        if v.__class__.__name__ == "RangeSpec":
            return v
        if isinstance(v, dict):
            return RangeSpec(**v)
        msg = "range_spec must be a RangeSpec object or a dict"
        raise ValueError(msg)


class DropDownMixin(BaseModel):
    """下拉参数混入。

    契约：
    - 输入：选项与切换配置
    - 输出：下拉配置
    - 副作用：影响下拉与对话框展示
    - 失败语义：`toggle_value` 非布尔时抛 `ValueError`
    """
    options: list[str] | None = None
    """下拉选项列表。仅在 `is_list=True` 时使用。"""
    options_metadata: list[dict[str, Any]] | None = None
    """选项元数据列表。"""
    combobox: CoalesceBool = False
    """是否允许自定义输入。"""
    dialog_inputs: dict[str, Any] | None = None
    """弹窗配置字典。"""
    toggle: bool = False
    """是否显示切换按钮。"""
    toggle_value: bool | None = None
    """切换按钮的当前值。"""
    toggle_disable: bool | None = None
    """切换按钮是否禁用。"""

    @field_validator("toggle_value")
    @classmethod
    def validate_toggle_value(cls, v):
        """校验切换按钮值类型。"""
        if v is not None and not isinstance(v, bool):
            msg = "toggle_value must be a boolean or None"
            raise ValueError(msg)
        return v


class SortableListMixin(BaseModel):
    """可排序列表混入。

    契约：
    - 输入：选项与分类配置
    - 输出：排序列表配置
    - 副作用：影响检索与展示
    - 失败语义：无
    """
    helper_text: str | None = None
    """辅助说明文本。"""
    helper_text_metadata: dict[str, Any] | None = None
    """辅助说明元数据。"""
    search_category: list[str] = Field(default=[])
    """检索分类标签。"""
    options: list[dict[str, Any]] = Field(default_factory=list)
    """可排序项的元数据列表。"""
    limit: int | None = None
    """展示数量上限。"""


class ConnectionMixin(BaseModel):
    """连接字段混入。

    契约：
    - 输入：连接链接与按钮元数据
    - 输出：连接展示配置
    - 副作用：影响连接入口呈现
    - 失败语义：无
    """
    helper_text: str | None = None
    """辅助说明文本。"""
    helper_text_metadata: dict[str, Any] | None = None
    """辅助说明元数据。"""
    connection_link: str | None = None
    """连接入口链接。"""
    button_metadata: dict[str, Any] | None = None
    """按钮元数据。"""
    search_category: list[str] = Field(default=[])
    """检索分类标签。"""
    options: list[dict[str, Any]] = Field(default_factory=list)
    """连接选项元数据列表。"""


class TabMixin(BaseModel):
    """Tab 输入混入（最多 3 项，每项最长 20 字符）。"""

    options: list[str] = Field(default_factory=list, max_length=3)
    """Tab 选项列表。"""

    @field_validator("options")
    @classmethod
    def validate_options(cls, v):
        """校验 Tab 选项数量与长度上限。"""
        max_tab_options = 3
        max_tab_option_length = 20

        if len(v) > max_tab_options:
            msg = f"Maximum of {max_tab_options} tab values allowed. Got {len(v)} values."
            raise ValueError(msg)

        for i, value in enumerate(v):
            if len(value) > max_tab_option_length:
                msg = (
                    f"Tab value at index {i} exceeds maximum length of {max_tab_option_length} "
                    f"characters. Got {len(value)} characters."
                )
                raise ValueError(msg)

        return v


class MultilineMixin(BaseModel):
    """多行输入标记混入。

    契约：
    - 输入：`multiline` 布尔值
    - 输出：多行配置
    - 副作用：影响文本框高度
    - 失败语义：无
    """
    multiline: CoalesceBool = True


class AIMixin(BaseModel):
    """AI 能力标记混入。

    契约：
    - 输入：`ai_enabled` 布尔值
    - 输出：能力标记
    - 副作用：影响前端能力提示
    - 失败语义：无
    """
    ai_enabled: CoalesceBool = False


class LinkMixin(BaseModel):
    """链接字段混入。

    契约：
    - 输入：`icon` 与 `text`
    - 输出：链接展示配置
    - 副作用：无
    - 失败语义：无
    """
    icon: str | None = None
    """链接图标名称。"""
    text: str | None = None
    """链接文本。"""


class SliderMixin(BaseModel):
    """滑块外观参数混入。

    契约：
    - 输入：标签与按钮配置
    - 输出：滑块外观配置
    - 副作用：影响 UI 展示
    - 失败语义：无
    """
    min_label: str = Field(default="")
    max_label: str = Field(default="")
    min_label_icon: str = Field(default="")
    max_label_icon: str = Field(default="")
    slider_buttons: bool = Field(default=False)
    slider_buttons_options: list[str] = Field(default=[])
    slider_input: bool = Field(default=False)


class TableMixin(BaseModel):
    """表格配置混入。

    契约：
    - 输入：表格 schema 与图标/按钮配置
    - 输出：表格展示配置
    - 副作用：影响表格 UI
    - 失败语义：无
    """
    # 注意：当前使用简化类型，完整实现可替换为更严格的 schema
    table_schema: dict | list | None = None
    trigger_text: str = Field(default="Open table")
    trigger_icon: str = Field(default="Table")
    table_icon: str = Field(default="Table")
    table_options: dict | None = None


class McpMixin(BaseModel):
    """MCP 输入混入。

    契约：
    - 输入：无
    - 输出：混入字段
    - 副作用：无
    - 失败语义：无
    """


class PromptFieldMixin(BaseModel):
    """Prompt 输入混入。

    契约：
    - 输入：无
    - 输出：混入字段
    - 副作用：无
    - 失败语义：无
    """


class ToolsMixin(BaseModel):
    """Tools 输入混入。

    契约：
    - 输入：无
    - 输出：混入字段
    - 副作用：无
    - 失败语义：无
    """
