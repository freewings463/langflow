"""
模块名称：文本输入组件

本模块提供 TextComponent，用于将文本或 Data 传递到下游组件，
并通过模板将 Data 转为文本输出配置。主要功能包括：
- 定义输入字段（支持 `Message`/`Data`）
- 提供可选模板用于 Data→Text 的转换

关键组件：TextComponent、build_config
设计背景：为简单文本传递场景提供统一配置与模板入口
注意事项：未填写模板时会在运行期动态使用 Data 的 `text` 字段
"""

from lfx.custom.custom_component.component import Component


class TextComponent(Component):
    """文本透传组件，提供输入配置与模板占位。
    契约：输出为组件配置字典；副作用为无。
    关键路径：生成 input_value/data_template 配置并返回。
    决策：模板为空时由运行期补齐。问题：提前未知 Data 字段名；方案：运行期选择 `text`；代价：依赖运行期约定；重评：当 Data 结构固定时。
    """

    display_name = "Text Component"
    description = "Used to pass text to the next component."

    def build_config(self):
        """生成组件配置定义。
        契约：返回包含 input_value 与 data_template 的配置字典。
        关键路径：构建字段配置 → 返回。
        决策：input_types 仅支持 `Message`/`Data`。问题：限制输入类型；方案：固定两类；代价：灵活性下降；重评：当需要支持更多类型时。
        """
        return {
            "input_value": {
                "display_name": "Value",
                "input_types": ["Message", "Data"],
                "info": "Text or Data to be passed.",
            },
            "data_template": {
                "display_name": "Data Template",
                "multiline": True,
                "info": "Template to convert Data to Text. "
                "If left empty, it will be dynamically set to the Data's text key.",
                "advanced": True,
            },
        }
