"""模块名称：前端节点模板模型

本模块定义前端节点的序列化与校验模型，供模板系统在前后端之间传递组件定义。
主要功能包括：
- FrontendNode 的字段契约与默认值
- 以 `name` 为外层键的序列化输出（兼容前端节点字典格式）
- 输入/输出命名冲突校验与历史字段迁移

关键组件：
- FrontendNode：节点模型与序列化/校验逻辑
- Output/Template：输出端口与字段模板定义

设计背景：前端需要稳定的节点结构，并兼容旧版 `output_types`。
注意事项：当 `outputs` 为空时会根据 `output_types` 迁移生成输出端口。
"""

from collections import defaultdict

from pydantic import BaseModel, field_serializer, model_serializer

from lfx.template.field.base import Output
from lfx.template.template.base import Template


class FrontendNode(BaseModel):
    """前端节点的数据契约与序列化入口。

    契约：
    - `name` 作为序列化外层键，必须稳定且唯一。
    - `template.fields` 提供前端渲染所需的字段定义。
    - `outputs` 为空时可由 `output_types` 迁移生成。
    """
    _format_template: bool = True
    template: Template
    """前端字段模板；应包含可渲染的 `fields` 列表。"""
    description: str | None = None
    """前端节点的说明文本，可为空。"""
    icon: str | None = None
    """前端图标标识（名称或路径），为空则由 UI 兜底。"""
    is_input: bool | None = None
    """是否作为图的输入节点；为 True 时需包含 `input_value` 字段。"""
    is_output: bool | None = None
    """是否作为图的输出节点；为 True 时需包含 `input_value` 字段。"""
    is_composition: bool | None = None
    """是否作为组合节点标记（影响前端组合/分组行为）。"""
    base_classes: list[str]
    """组件基类列表；序列化时会去重并按字母序稳定输出。"""
    name: str = ""
    """节点唯一标识；用于序列化外层键。"""
    display_name: str | None = ""
    """前端展示名；为空时回退到 `name`。"""
    priority: int | None = None
    """排序权重（语义由调用方定义）。"""
    documentation: str = ""
    """节点文档（通常为 Markdown/富文本）。"""
    minimized: bool = False
    """是否默认折叠显示。"""
    custom_fields: dict | None = defaultdict(list)
    """前端扩展字段的透传容器。"""
    output_types: list[str] = []
    """旧版输出类型列表（用于兼容迁移）。"""
    full_path: str | None = None
    """组件全路径，用于定位与检索。"""
    pinned: bool = False
    """是否在前端置顶。"""
    conditional_paths: list[str] = []
    """条件路径标识列表（前端用于条件展示）。"""
    frozen: bool = False
    """是否冻结（前端禁止编辑）。"""
    outputs: list[Output] = []
    """输出端口定义列表。"""

    field_order: list[str] = []
    """字段显示顺序（仅影响 UI 排列）。"""
    beta: bool = False
    """是否标记为 Beta。"""
    legacy: bool = False
    """是否标记为 Legacy。"""
    replacement: list[str] | None = None
    """弃用后的替代节点名称列表。"""
    error: str | None = None
    """节点级错误信息（展示给前端/调用方）。"""
    edited: bool = False
    """是否已在前端被编辑。"""
    metadata: dict = {}
    """透传元数据（避免放置体积过大的对象）。"""
    tool_mode: bool = False
    """是否处于工具模式。"""

    def set_documentation(self, documentation: str) -> None:
        """设置节点文档（不会触发额外副作用）。"""
        self.documentation = documentation

    @field_serializer("base_classes")
    def process_base_classes(self, base_classes: list[str]) -> list[str]:
        """规范化基类列表：去重并按小写排序以保证序列化稳定性。"""
        return sorted(set(base_classes), key=lambda x: x.lower())

    @field_serializer("display_name")
    def process_display_name(self, display_name: str) -> str:
        """为空时回退到 `name`，避免前端出现空标题。"""
        return display_name or self.name

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        """序列化为前端期望的节点字典结构。

        关键路径（三步）：
        1) 先由 Pydantic 生成基础字典（`handler(self)`）
        2) 按需将 `Template` 转为 dict，并做 `output_types` -> `outputs` 迁移
        3) 使用 `name` 作为外层键返回 `{name: payload}`

        异常流：依赖 Pydantic 序列化失败时抛出原始异常。
        排障入口：检查输出是否缺失 `outputs` 或 `template` 字段。
        """
        result = handler(self)
        if hasattr(self, "template") and hasattr(self.template, "to_dict"):
            result["template"] = self.template.to_dict()
        name = result.pop("name")

        # 迁移：旧版 `output_types` 需要转换成 `outputs` 端口结构
        if "output_types" in result and not result.get("outputs"):
            for base_class in result["output_types"]:
                output = Output(
                    display_name=base_class,
                    name=base_class.lower(),
                    types=[base_class],
                    selected=base_class,
                )
                result["outputs"].append(output.model_dump())

        return {name: result}

    @classmethod
    def from_dict(cls, data: dict) -> "FrontendNode":
        """从字典构建节点模型，必要时将 `template` 反序列化为 `Template`。"""
        if "template" in data:
            data["template"] = Template.from_dict(data["template"])
        return cls(**data)

    # 兼容：保留旧调用方式的输出结构
    def to_dict(self, *, keep_name=True) -> dict:
        """以字典形式输出；`keep_name=False` 时返回去掉外层键的 payload。"""
        dump = self.model_dump(by_alias=True, exclude_none=True)
        if not keep_name:
            return dump.pop(self.name)
        return dump

    def add_extra_fields(self) -> None:
        """扩展点：子类可在此追加前端字段。"""
        pass

    def add_extra_base_classes(self) -> None:
        """扩展点：子类可在此追加基类名称。"""
        pass

    def set_base_classes_from_outputs(self) -> None:
        """根据 `outputs` 回填 `base_classes`（用于兼容依赖基类列表的调用方）。"""
        self.base_classes = [output_type for output in self.outputs for output_type in output.types]

    def validate_component(self) -> None:
        """执行节点命名冲突与保留字段校验。"""
        self.validate_name_overlap()
        self.validate_attributes()

    def validate_name_overlap(self) -> None:
        """校验输入与输出名称不重叠；冲突时抛出 `ValueError`。"""
        # 检查输出端口名称与输入字段名称是否重叠
        output_names = [output.name for output in self.outputs if not output.allows_loop]
        input_names = [input_.name for input_ in self.template.fields]
        overlap = set(output_names).intersection(input_names)
        if overlap:
            overlap_str = ", ".join(f"'{x}'" for x in overlap)
            msg = (
                "There should be no overlap between input and output names. "
                f"Names {overlap_str} are duplicated in component {self.display_name}. "
                f"Inputs are {input_names} and outputs are {output_names}."
            )
            raise ValueError(msg)

    def validate_attributes(self) -> None:
        """避免与运行时保留属性冲突（输出/输入名不能占用保留字段）。"""
        # 保留属性：避免与运行时注入字段冲突
        output_names = [output.name for output in self.outputs]
        input_names = [input_.name for input_ in self.template.fields]
        attributes = [
            "inputs",
            "outputs",
            "_artifacts",
            "_results",
            "logs",
            "status",
            "vertex",
            "graph",
            "display_name",
            "description",
            "documentation",
            "icon",
        ]
        output_overlap = set(output_names).intersection(attributes)
        input_overlap = set(input_names).intersection(attributes)
        error_message = ""
        if output_overlap:
            output_overlap_str = ", ".join(f"'{x}'" for x in output_overlap)
            error_message += f"Output names {output_overlap_str} are reserved attributes.\n"
        if input_overlap:
            input_overlap_str = ", ".join(f"'{x}'" for x in input_overlap)
            error_message += f"Input names {input_overlap_str} are reserved attributes."

    def add_base_class(self, base_class: str | list[str]) -> None:
        """追加基类名称；支持单个或批量追加。"""
        if isinstance(base_class, str):
            self.base_classes.append(base_class)
        elif isinstance(base_class, list):
            self.base_classes.extend(base_class)

    def add_output_type(self, output_type: str | list[str]) -> None:
        """追加旧版输出类型；用于兼容依赖 `output_types` 的调用方。"""
        if isinstance(output_type, str):
            self.output_types.append(output_type)
        elif isinstance(output_type, list):
            self.output_types.extend(output_type)

    @classmethod
    def from_inputs(cls, **kwargs):
        """由输入字段快速构建节点；缺少 `inputs` 时抛出 `ValueError`。"""
        if "inputs" not in kwargs:
            msg = "Missing 'inputs' argument."
            raise ValueError(msg)
        if "_outputs_map" in kwargs:
            kwargs["outputs"] = kwargs.pop("_outputs_map")
        inputs = kwargs.pop("inputs")
        template = Template(type_name="Component", fields=inputs)
        kwargs["template"] = template
        return cls(**kwargs)

    def set_field_value_in_template(self, field_name, value) -> None:
        """按字段名替换 `template.fields` 中对应值（原字段保持不可变拷贝）。"""
        for idx, field in enumerate(self.template.fields):
            if field.name == field_name:
                new_field = field.model_copy()
                new_field.value = value
                self.template.fields[idx] = new_field
                break

    def set_field_load_from_db_in_template(self, field_name, value) -> None:
        """仅当字段支持 `load_from_db` 时更新该标志位。"""
        for idx, field in enumerate(self.template.fields):
            if field.name == field_name and hasattr(field, "load_from_db"):
                new_field = field.model_copy()
                new_field.load_from_db = value
                self.template.fields[idx] = new_field
                break
