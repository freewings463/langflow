"""
模块名称：自定义组件代码执行入口

本模块提供组件代码的解析与类构造入口，供上层动态加载自定义组件。
主要功能：
- 提取组件类名；
- 构造组件类并返回类型对象。

设计背景：集中管理动态代码执行入口，便于统一校验与错误处理。
注意事项：调用方需保证代码可信并已通过校验。
"""

from typing import TYPE_CHECKING

from lfx.custom import validate

if TYPE_CHECKING:
    from lfx.custom.custom_component.custom_component import CustomComponent


def eval_custom_component_code(code: str) -> type["CustomComponent"]:
    """解析并构造自定义组件类

    契约：返回自定义组件类对象；失败抛出校验异常。
    关键路径：1) 提取类名 2) 动态创建类。
    """
    class_name = validate.extract_class_name(code)
    return validate.create_class(code, class_name)
