"""表格 schema 与校验枚举。"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VALID_TYPES = [
    "date",
    "number",
    "text",
    "json",
    "integer",
    "int",
    "float",
    "str",
    "string",
    "boolean",
]


class FormatterType(str, Enum):
    """字段格式化类型。"""
    date = "date"
    text = "text"
    number = "number"
    json = "json"
    boolean = "boolean"


class EditMode(str, Enum):
    """字段编辑模式。"""
    MODAL = "modal"
    POPOVER = "popover"
    INLINE = "inline"


class Column(BaseModel):
    """表格列定义。"""
    model_config = ConfigDict(populate_by_name=True)
    name: str
    display_name: str = Field(default="")
    options: list[str] | None = Field(default=None)
    sortable: bool = Field(default=True)
    filterable: bool = Field(default=True)
    formatter: FormatterType | str | None = Field(default=None)
    type: FormatterType | str | None = Field(default=None)
    description: str | None = None
    default: str | bool | int | float | None = None
    disable_edit: bool = Field(default=False)
    edit_mode: EditMode | None = Field(default=EditMode.POPOVER)
    hidden: bool = Field(default=False)
    load_from_db: bool = Field(default=False)
    """Whether this column's default value should be loaded from global variables"""

    @model_validator(mode="after")
    def set_display_name(self):
        if not self.display_name:
            self.display_name = self.name
        return self

    @model_validator(mode="after")
    def set_formatter_from_type(self):
        if self.type and not self.formatter:
            self.formatter = self.validate_formatter(self.type)
        if self.formatter in {"boolean", "bool"}:
            valid_trues = ["True", "true", "1", "yes"]
            valid_falses = ["False", "false", "0", "no"]
            if self.default in valid_trues:
                self.default = True
            if self.default in valid_falses:
                self.default = False
        elif self.formatter in {"integer", "int"}:
            self.default = int(self.default)
        elif self.formatter in {"float"}:
            self.default = float(self.default)
        else:
            self.default = str(self.default)
        return self

    @field_validator("formatter", mode="before")
    @classmethod
    def validate_formatter(cls, value):
        if value in {"boolean", "bool"}:
            value = FormatterType.boolean
        if value in {"integer", "int", "float"}:
            value = FormatterType.number
        if value in {"str", "string"}:
            value = FormatterType.text
        if value == "dict":
            value = FormatterType.json
        if value == "date":
            value = FormatterType.date
        if isinstance(value, str):
            return FormatterType(value)
        if isinstance(value, FormatterType):
            return value
        msg = f"Invalid formatter type: {value}. Valid types are: {FormatterType}"
        raise ValueError(msg)


class TableSchema(BaseModel):
    """表格列集合。"""
    columns: list[Column]


class FieldValidatorType(str, Enum):
    """字段校验类型枚举。"""

    NO_SPACES = "no_spaces"  # 禁止空格
    LOWERCASE = "lowercase"  # 强制小写
    UPPERCASE = "uppercase"  # 强制大写
    EMAIL = "email"  # 邮箱格式
    URL = "url"  # URL 格式
    ALPHANUMERIC = "alphanumeric"  # 仅字母与数字
    NUMERIC = "numeric"  # 仅数字
    ALPHA = "alpha"  # 仅字母
    PHONE = "phone"  # 电话格式
    SLUG = "slug"  # URL slug（小写+连字符）
    USERNAME = "username"  # 字母数字+下划线
    PASSWORD = "password"  # 最低安全要求


class FieldParserType(str, Enum):
    """字段解析类型枚举。"""

    SNAKE_CASE = "snake_case"
    CAMEL_CASE = "camel_case"
    PASCAL_CASE = "pascal_case"
    KEBAB_CASE = "kebab_case"
    LOWERCASE = "lowercase"
    UPPERCASE = "uppercase"
    NO_BLANK = "no_blank"
    VALID_CSV = ("valid_csv",)
    COMMANDS = "commands"


class TableOptions(BaseModel):
    """表格行为与校验选项。"""
    block_add: bool = Field(default=False)
    block_delete: bool = Field(default=False)
    block_edit: bool = Field(default=False)
    block_sort: bool = Field(default=False)
    block_filter: bool = Field(default=False)
    block_hide: bool | list[str] = Field(default=False)
    block_select: bool = Field(default=False)
    hide_options: bool = Field(default=False)
    field_validators: dict[str, list[FieldValidatorType] | FieldValidatorType] | None = Field(default=None)
    field_parsers: dict[str, list[FieldParserType] | FieldParserType] | None = Field(default=None)
    description: str | None = Field(default=None)
