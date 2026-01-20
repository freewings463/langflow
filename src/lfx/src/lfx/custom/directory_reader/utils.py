"""
模块名称：目录读取工具函数

本模块提供目录读取与组件菜单构建的辅助函数，支持同步与异步流程。
主要功能：
- 合并菜单结构并处理重名；
- 构建有效/无效组件菜单；
- 从路径加载并生成组件列表。

设计背景：拆分 DirectoryReader 的辅助逻辑，提升复用性与可测试性。
注意事项：部分函数依赖 DirectoryReader 的输出结构约定。
"""

import asyncio

from lfx.custom.directory_reader.directory_reader import DirectoryReader
from lfx.log.logger import logger
from lfx.template.frontend_node.custom_components import CustomComponentFrontendNode


def merge_nested_dicts_with_renaming(dict1, dict2):
    """合并嵌套字典并处理子键覆盖

    契约：将 dict2 的内容合并到 dict1，返回合并后的 dict1。
    关键路径：1) 递归合并同名子字典 2) 覆盖/新增键。
    决策：子键冲突时直接覆盖
    问题：菜单结构可能存在重名
    方案：以 dict2 为准覆盖
    代价：可能丢失 dict1 原值
    重评：当需要保留重复键时
    """
    for key, value in dict2.items():
        if key in dict1 and isinstance(value, dict) and isinstance(dict1.get(key), dict):
            for sub_key, sub_value in value.items():
                # 注意：当前策略直接覆盖同名子键。
                dict1[key][sub_key] = sub_value
        else:
            dict1[key] = value
    return dict1


def build_invalid_menu(invalid_components):
    """构建无效组件菜单

    契约：若无无效组件返回空 dict；否则返回按菜单聚合的结构。
    """
    if not invalid_components.get("menu"):
        return {}

    logger.debug("------------------- INVALID COMPONENTS -------------------")
    invalid_menu = {}
    for menu_item in invalid_components["menu"]:
        menu_name = menu_item["name"]
        invalid_menu[menu_name] = build_invalid_menu_items(menu_item)
    return invalid_menu


def build_valid_menu(valid_components):
    """构建有效组件菜单

    契约：返回按菜单分组的有效组件结构。
    """
    valid_menu = {}
    logger.debug("------------------- VALID COMPONENTS -------------------")
    for menu_item in valid_components["menu"]:
        menu_name = menu_item["name"]
        valid_menu[menu_name] = build_menu_items(menu_item)
    return valid_menu


def build_and_validate_all_files(reader: DirectoryReader, file_list):
    """同步构建并校验所有文件

    契约：返回 `(valid_components, invalid_components)`。
    关键路径：1) 构建菜单列表 2) 过滤有效/无效组件。
    """
    data = reader.build_component_menu_list(file_list)

    valid_components = reader.filter_loaded_components(data=data, with_errors=False)
    invalid_components = reader.filter_loaded_components(data=data, with_errors=True)

    return valid_components, invalid_components


async def abuild_and_validate_all_files(reader: DirectoryReader, file_list):
    """异步构建并校验所有文件

    契约：返回 `(valid_components, invalid_components)`。
    关键路径：1) 异步构建菜单列表 2) 过滤有效/无效组件。
    """
    data = await reader.abuild_component_menu_list(file_list)

    valid_components = reader.filter_loaded_components(data=data, with_errors=False)
    invalid_components = reader.filter_loaded_components(data=data, with_errors=True)

    return valid_components, invalid_components


def load_files_from_path(path: str):
    """从路径加载文件列表

    契约：返回目录下符合规则的 Python 文件列表。
    """
    reader = DirectoryReader(path, compress_code_field=False)

    return reader.get_files()


def build_custom_component_list_from_path(path: str):
    """从路径构建自定义组件列表（同步）

    契约：返回合并后的菜单结构，包含有效与无效组件。
    """
    file_list = load_files_from_path(path)
    reader = DirectoryReader(path, compress_code_field=False)

    valid_components, invalid_components = build_and_validate_all_files(reader, file_list)

    valid_menu = build_valid_menu(valid_components)
    invalid_menu = build_invalid_menu(invalid_components)

    return merge_nested_dicts_with_renaming(valid_menu, invalid_menu)


async def abuild_custom_component_list_from_path(path: str):
    """从路径构建自定义组件列表（异步）

    契约：返回合并后的菜单结构，包含有效与无效组件。
    """
    file_list = await asyncio.to_thread(load_files_from_path, path)
    reader = DirectoryReader(path, compress_code_field=False)

    valid_components, invalid_components = await abuild_and_validate_all_files(reader, file_list)

    valid_menu = build_valid_menu(valid_components)
    invalid_menu = build_invalid_menu(invalid_components)

    return merge_nested_dicts_with_renaming(valid_menu, invalid_menu)


def create_invalid_component_template(component, component_name):
    """创建无效组件的占位模板

    契约：返回前端节点模板字典，包含错误信息与源码。
    关键路径：1) 创建前端节点 2) 写入错误与代码字段。
    """
    component_code = component["code"]
    component_frontend_node = CustomComponentFrontendNode(
        description="ERROR - Check your Python Code",
        display_name=f"ERROR - {component_name}",
    )

    component_frontend_node.error = component.get("error", None)
    field = component_frontend_node.template.get_field("code")
    field.value = component_code
    component_frontend_node.template.update_field("code", field)
    return component_frontend_node.model_dump(by_alias=True, exclude_none=True)


def log_invalid_component_details(component) -> None:
    """记录无效组件的日志详情。"""
    logger.debug(component)
    logger.debug(f"Component Path: {component.get('path', None)}")
    logger.debug(f"Component Error: {component.get('error', None)}")


def build_invalid_component(component):
    """构建单个无效组件条目

    契约：返回 `(component_name, component_template)`。
    """
    component_name = component["name"]
    component_template = create_invalid_component_template(component, component_name)
    log_invalid_component_details(component)
    return component_name, component_template


def build_invalid_menu_items(menu_item):
    """构建指定菜单的无效组件列表

    契约：返回 `{component_name: component_template}` 字典。
    异常流：单组件失败记录日志并跳过。
    """
    menu_items = {}
    for component in menu_item["components"]:
        try:
            component_name, component_template = build_invalid_component(component)
            menu_items[component_name] = component_template
            logger.debug(f"Added {component_name} to invalid menu.")
        except Exception:  # noqa: BLE001  # 注意：无效组件构建失败需隔离处理。
            logger.exception(f"Error while creating custom component [{component_name}]")
    return menu_items


def get_new_key(dictionary, original_key):
    """生成不冲突的新键名。"""
    counter = 1
    new_key = original_key + " (" + str(counter) + ")"
    while new_key in dictionary:
        counter += 1
        new_key = original_key + " (" + str(counter) + ")"
    return new_key


def determine_component_name(component):
    """确定组件显示名称。"""
    # 注意：保留历史逻辑以便回溯组件命名策略。
    # 旧逻辑（保留注释以便回溯）：
    # component_output_types = component["output_types"]
    # if len(component_output_types) == 1:
    #     return component_output_types[0]
    # else:
    #     file_name = component.get("file").split(".")[0]
    #     return "".join(word.capitalize() for word in file_name.split("_")) if "_" in file_name else file_name
    return component["name"]


def build_menu_items(menu_item):
    """构建指定菜单的有效组件列表

    契约：返回 `{component_name: component_template}` 字典。
    异常流：单组件失败记录日志并跳过。
    """
    menu_items = {}
    logger.debug(f"Building menu items for {menu_item['name']}")
    logger.debug(f"Loading {len(menu_item['components'])} components")
    for component_name, component_template, component in menu_item["components"]:
        try:
            menu_items[component_name] = component_template
        except Exception:  # noqa: BLE001  # 注意：组件构建失败需隔离处理。
            logger.exception(f"Error while building custom component {component['output_types']}")
    return menu_items
