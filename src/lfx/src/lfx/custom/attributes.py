"""
模块名称：自定义组件属性解析

本模块提供组件属性的安全提取与类型规范化逻辑，避免元数据字段类型不一致。
主要功能：
- 校验图标字段（emoji）格式；
- 按字段类型返回安全值；
- 维护属性名到处理函数的映射表。

设计背景：组件元数据来自用户代码，需在解析阶段做类型兜底与校验。
注意事项：emoji 校验允许 `:emoji_name:` 语法，但不强制替换非法值。
"""

from collections.abc import Callable

import emoji

from lfx.log.logger import logger


def validate_icon(value: str):
    """校验并转换 emoji 字符串

    契约：返回合法 emoji 或原始值；非法格式抛 `ValueError`。
    关键路径：1) 校验冒号包裹 2) 使用 emoji 库转换 3) 失败则返回原值。
    决策：不强制替换非法 emoji
    问题：用户可能输入无法识别的 emoji 名
    方案：保留原值并记录警告
    代价：UI 可能显示原始文本
    重评：当需要严格校验并阻止保存时
    """
    # 注意：emoji 允许使用 `:emoji_name:` 形式定义。

    if not value.startswith(":") and not value.endswith(":"):
        return value
    if not value.startswith(":") or not value.endswith(":"):
        # 注意：emoji 需同时包含起止冒号，否则视为非法。
        msg = f"Invalid emoji. {value} is not a valid emoji."
        raise ValueError(msg)

    emoji_value = emoji.emojize(value, variant="emoji_type")
    if value == emoji_value:
        logger.warning(f"Invalid emoji. {value} is not a valid emoji.")
        return value
    return emoji_value


def getattr_return_str(value):
    """安全返回字符串字段。"""
    return str(value) if value else ""


def getattr_return_bool(value):
    """安全返回布尔字段。"""
    if isinstance(value, bool):
        return value
    return None


def getattr_return_int(value):
    """安全返回整数字段。"""
    if isinstance(value, int):
        return value
    return None


def getattr_return_list_of_str(value):
    """安全返回字符串列表。"""
    if isinstance(value, list):
        return [str(val) for val in value]
    return []


def getattr_return_list_of_object(value):
    """安全返回对象列表。"""
    if isinstance(value, list):
        return value
    return []


def getattr_return_list_of_values_from_dict(value):
    """从字典中提取 value 列表。"""
    if isinstance(value, dict):
        return list(value.values())
    return []


def getattr_return_dict(value):
    """安全返回字典字段。"""
    if isinstance(value, dict):
        return value
    return {}


ATTR_FUNC_MAPPING: dict[str, Callable] = {
    "display_name": getattr_return_str,
    "description": getattr_return_str,
    "beta": getattr_return_bool,
    "legacy": getattr_return_bool,
    "replacement": getattr_return_list_of_str,
    "documentation": getattr_return_str,
    "priority": getattr_return_int,
    "icon": validate_icon,
    "minimized": getattr_return_bool,
    "frozen": getattr_return_bool,
    "is_input": getattr_return_bool,
    "is_output": getattr_return_bool,
    "conditional_paths": getattr_return_list_of_str,
    "_outputs_map": getattr_return_list_of_values_from_dict,
    "_inputs": getattr_return_list_of_values_from_dict,
    "outputs": getattr_return_list_of_object,
    "inputs": getattr_return_list_of_object,
    "metadata": getattr_return_dict,
    "tool_mode": getattr_return_bool,
}
