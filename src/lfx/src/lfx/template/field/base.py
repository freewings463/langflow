"""
模块名称：template.field.base

本模块定义模板字段的输入/输出模型与序列化规则，用于统一前端字段渲染与类型约束。
主要功能包括：
- Input/Output 字段模型及其序列化逻辑
- 类型标准化与默认值补全
- 输出选项与数据过滤的应用

关键组件：
- Input：输入字段模型
- Output：输出字段模型
- OutputOptions：输出过滤选项

设计背景：模板字段需要在 UI 与执行层共享结构化元数据，避免各处重复定义。
注意事项：类型序列化会影响前端渲染，修改需同步评估兼容性。
"""

from collections.abc import Callable
from enum import Enum
from typing import (  # type: ignore[attr-defined]
    Any,
    GenericAlias,  # type: ignore[attr-defined]
    _GenericAlias,  # type: ignore[attr-defined]
    _UnionGenericAlias,  # type: ignore[attr-defined]
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from lfx.field_typing import Text
from lfx.field_typing.range_spec import RangeSpec
from lfx.helpers.custom import format_type
from lfx.schema.data import Data
from lfx.type_extraction import post_process_type


class UndefinedType(Enum):
    """内部占位类型，用于区分“未定义”与显式空值。"""

    undefined = "__UNDEFINED__"


UNDEFINED = UndefinedType.undefined


class Input(BaseModel):
    """输入字段模型。

    契约：
    - 输入：字段元数据与默认值
    - 输出：可序列化的输入字段定义
    - 副作用：序列化时可能补全 `input_types`/`display_name`
    - 失败语义：类型不合法时抛 ValueError
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    field_type: str | type | None = Field(default=str, serialization_alias="type")
    """字段类型，默认 `str`。"""

    required: bool = False
    """是否必填，默认 False。"""

    placeholder: str = ""
    """输入占位文本。"""

    is_list: bool = Field(default=False, serialization_alias="list")
    """是否为列表字段。"""

    show: bool = True
    """是否在 UI 中展示。"""

    multiline: bool = False
    """是否允许多行编辑。"""

    value: Any = None
    """字段默认值。"""

    file_types: list[str] = Field(default=[], serialization_alias="fileTypes")
    """允许的文件类型列表。"""

    file_path: str | None = ""
    """文件字段的路径值。"""

    password: bool | None = None
    """是否为密码输入。"""

    options: list[str] | Callable | None = None
    """可选项列表或动态生成函数（多选字段使用）。"""

    name: str | None = None
    """字段内部名称。"""

    display_name: str | None = None
    """字段展示名称。"""

    advanced: bool = False
    """是否为高级参数（可隐藏）。"""

    input_types: list[str] | None = None
    """多类型字段的输入类型列表。"""

    dynamic: bool = False
    """是否为动态字段。"""

    info: str | None = ""
    """提示信息（tooltip 文案）。"""

    real_time_refresh: bool | None = None
    """是否实时刷新（启用时需关闭 `refresh_button`）。"""

    refresh_button: bool | None = None
    """是否显示刷新按钮。"""

    refresh_button_text: str | None = None
    """刷新按钮的显示文案。"""

    range_spec: RangeSpec | None = Field(default=None, serialization_alias="rangeSpec")
    """数值范围配置。"""

    load_from_db: bool = False
    """是否从数据库加载默认值。"""

    title_case: bool = False
    """是否将显示名转为标题格式。"""

    def to_dict(self):
        """序列化为 dict（使用别名并排除空值）。"""
        return self.model_dump(by_alias=True, exclude_none=True)

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        result = handler(self)
        # 注意：字段类型为 str/Text 时，补充 Text 输入类型
        if self.field_type in {"str", "Text"} and "input_types" not in result:
            result["input_types"] = ["Text"]
        if self.field_type == Text:
            result["type"] = "str"
        else:
            result["type"] = self.field_type
        return result

    @model_validator(mode="after")
    def validate_model(self):
        # 注意：整型字段需要补齐步进规则
        if self.field_type == "int" and self.range_spec is not None:
            self.range_spec = RangeSpec.set_step_type("int", self.range_spec)
        return self

    @field_serializer("file_path")
    def serialize_file_path(self, value):
        """仅在文件字段时输出 file_path。"""
        return value if self.field_type == "file" else ""

    @field_serializer("field_type")
    def serialize_field_type(self, value, _info):
        """序列化时补齐浮点范围配置。"""
        if value is float and self.range_spec is None:
            self.range_spec = RangeSpec()
        return value

    @field_serializer("display_name")
    def serialize_display_name(self, value, _info):
        # 注意：未显式提供展示名时，使用 name 并按需转标题格式
        if value is None:
            # name 通常为 snake_case
            # 示例："file_path" -> "File Path"
            value = self.name.replace("_", " ")
            if self.title_case:
                value = value.title()
        return value

    @field_validator("file_types")
    @classmethod
    def validate_file_types(cls, value):
        if not isinstance(value, list):
            msg = "file_types must be a list"
            raise ValueError(msg)  # noqa: TRY004
        return [
            (f".{file_type}" if isinstance(file_type, str) and not file_type.startswith(".") else file_type)
            for file_type in value
        ]

    @field_validator("field_type", mode="before")
    @classmethod
    def validate_type(cls, v):
        # 注意：若传入类型对象而非字符串，需要统一格式化为字符串
        if isinstance(v, type | _GenericAlias | GenericAlias | _UnionGenericAlias):
            v = post_process_type(v)[0]
            v = format_type(v)
        elif not isinstance(v, str):
            msg = f"type must be a string or a type, not {type(v)}"
            raise ValueError(msg)  # noqa: TRY004
        return v


class OutputOptions(BaseModel):
    """输出过滤配置。"""

    filter: str | None = None
    """对输出数据应用的过滤表达式。"""


class Output(BaseModel):
    """输出字段模型。

    契约：
    - 输入：输出类型、默认值与可选项
    - 输出：可序列化的输出字段定义
    - 副作用：序列化时会将 UNDEFINED 显式转为占位值
    - 失败语义：缺少 name 时抛 ValueError
    """

    types: list[str] = Field(default=[])
    """输出类型列表。"""

    selected: str | None = Field(default=None)
    """当前选中的输出类型。"""

    name: str = Field(description="The name of the field.")
    """字段名称。"""

    hidden: bool | None = Field(default=None)
    """是否隐藏该字段。"""

    display_name: str | None = Field(default=None)
    """字段显示名。"""

    method: str | None = Field(default=None)
    """输出使用的方法名。"""

    value: Any | None = Field(default=UNDEFINED)
    """输出结果，占位值表示未计算。"""

    cache: bool = Field(default=True)

    required_inputs: list[str] | None = Field(default=None)
    """该输出依赖的必需输入列表。"""

    allows_loop: bool = Field(default=False)
    """是否允许循环输入。"""

    loop_types: list[str] | None = Field(default=None)
    """循环输入允许的额外类型。"""

    group_outputs: bool = Field(default=False)
    """是否将输出分组展示（无下拉）。"""

    options: OutputOptions | None = Field(default=None)
    """输出的过滤选项。"""

    tool_mode: bool = Field(default=True)
    """是否作为工具输出使用。"""

    def to_dict(self):
        """序列化为 dict（使用别名并排除空值）。"""
        return self.model_dump(by_alias=True, exclude_none=True)

    def add_types(self, type_: list[Any]) -> None:
        """追加输出类型并自动选择默认类型。"""
        if self.types is None:
            self.types = []
        self.types.extend([t for t in type_ if t not in self.types])
        # 注意：未选择类型时默认取第一个
        if self.selected is None and self.types:
            self.selected = self.types[0]

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        """序列化时将 UNDEFINED 替换为占位字符串。"""
        result = handler(self)
        if self.value == UNDEFINED:
            result["value"] = UNDEFINED.value
        return result

    @model_validator(mode="after")
    def validate_model(self):
        if self.value == UNDEFINED.value:
            self.value = UNDEFINED
        if self.name is None:
            msg = "name must be set"
            raise ValueError(msg)
        if self.display_name is None:
            self.display_name = self.name
        # 注意：兼容 dict 形式的 options
        if isinstance(self.options, dict):
            self.options = OutputOptions(**self.options)
        return self

    def apply_options(self, result):
        """应用输出过滤选项。"""
        if not self.options:
            return result
        if self.options.filter and isinstance(result, Data):
            return result.filter_data(self.options.filter)
        return result
