"""模块名称：云环境约束校验

模块目的：在 Astra 云环境中执行组件禁用与运行约束。
主要功能：
- 通过环境变量判断是否处于 Astra 云
- 按组件类型过滤禁用模块
使用场景：云部署的功能裁剪与合规限制。
关键组件：`is_astra_cloud_environment`、`ASTRA_CLOUD_DISABLED_COMPONENTS`
设计背景：云部署需要禁用部分高风险或受限组件。
注意事项：判断结果依赖环境变量，运行期变更需重启生效。
"""

import os
from typing import Any


def is_astra_cloud_environment() -> bool:
    """判断是否处于 Astra 云环境。"""
    disable_component = os.getenv("ASTRA_CLOUD_DISABLE_COMPONENT", "false")
    return disable_component.lower().strip() == "true"


def raise_error_if_astra_cloud_disable_component(msg: str):
    """若处于 Astra 云环境则抛出错误。"""
    if is_astra_cloud_environment():
        raise ValueError(msg)


# 注意：Astra 云环境中禁用的组件集合（模块名与组件名均需覆盖）。
ASTRA_CLOUD_DISABLED_COMPONENTS: dict[str, set[str]] = {
    "docling": {
        # 模块文件名（用于动态加载）
        "chunk_docling_document",
        "docling_inline",
        "export_docling_document",
        # 组件名称（用于索引/缓存加载）
        "ChunkDoclingDocument",
        "DoclingInline",
        "ExportDoclingDocument",
    }
}


def is_component_disabled_in_astra_cloud(component_type: str, module_filename: str) -> bool:
    """判断组件模块在 Astra 云环境中是否应被禁用。"""
    if not is_astra_cloud_environment():
        return False

    disabled_modules = ASTRA_CLOUD_DISABLED_COMPONENTS.get(component_type.lower(), set())
    return module_filename in disabled_modules


def filter_disabled_components_from_dict(modules_dict: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从已加载的模块字典中过滤禁用组件。

    关键路径：
    1) 非云环境直接返回原字典
    2) 命中禁用集合则过滤组件
    3) 无禁用规则则保留全部
    """
    if not is_astra_cloud_environment():
        return modules_dict

    filtered_dict: dict[str, dict[str, Any]] = {}
    for component_type, components in modules_dict.items():
        disabled_set = ASTRA_CLOUD_DISABLED_COMPONENTS.get(component_type.lower(), set())
        if disabled_set:
            # 过滤掉禁用组件
            filtered_components = {name: comp for name, comp in components.items() if name not in disabled_set}
            if filtered_components:  # 仅在仍有组件时保留该类型
                filtered_dict[component_type] = filtered_components
        else:
            # 没有禁用规则则保留全部
            filtered_dict[component_type] = components

    return filtered_dict
