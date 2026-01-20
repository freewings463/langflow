"""模块名称：自定义组件前端节点

本模块提供用于前端展示与编辑的自定义组件节点模板。
主要功能包括：
- 提供默认的自定义组件代码骨架
- 构建 `CustomComponent` 与 `Component` 两类前端节点
- 预置代码字段为可编辑模板

设计背景：前端需要一个可直接编辑的代码入口以快速创建组件。
注意事项：默认代码仅作示例，实际执行由后端组件加载流程决定。
"""

from lfx.template.field.base import Input
from lfx.template.frontend_node.base import FrontendNode
from lfx.template.template.base import Template

# 默认代码骨架：前端首次打开时的可编辑模板。
DEFAULT_CUSTOM_COMPONENT_CODE = """from lfx.custom import CustomComponent

from typing import Optional, List, Dict, Union
from lfx.field_typing import (
    Tool,
)
from lfx.schema.data import Data


class Component(CustomComponent):
    display_name: str = "Custom Component"
    description: str = "Create any custom component you want!"

    def build_config(self):
        return {"param": {"display_name": "Parameter"}}

    def build(self, param: Data) -> Data:
        return param

"""


class CustomComponentFrontendNode(FrontendNode):
    """自定义组件节点（展示为 CustomComponent 入口）。"""
    _format_template: bool = False
    name: str = "CustomComponent"
    display_name: str | None = "CustomComponent"
    beta: bool = False
    legacy: bool = False
    minimized: bool = False
    template: Template = Template(
        type_name="CustomComponent",
        fields=[
            Input(
                field_type="code",
                required=True,
                placeholder="",
                is_list=False,
                show=True,
                value=DEFAULT_CUSTOM_COMPONENT_CODE,
                name="code",
                advanced=False,
                dynamic=True,
            )
        ],
    )
    description: str | None = None
    base_classes: list[str] = []
    last_updated: str | None = None


class ComponentFrontendNode(FrontendNode):
    """通用组件节点（提供与自定义组件一致的代码编辑入口）。"""
    _format_template: bool = False
    name: str = "Component"
    display_name: str | None = "Component"
    beta: bool = False
    minimized: bool = False
    legacy: bool = False
    template: Template = Template(
        type_name="Component",
        fields=[
            Input(
                field_type="code",
                required=True,
                placeholder="",
                is_list=False,
                show=True,
                value=DEFAULT_CUSTOM_COMPONENT_CODE,
                name="code",
                advanced=False,
                dynamic=True,
            )
        ],
    )
    description: str | None = None
    base_classes: list[str] = []
