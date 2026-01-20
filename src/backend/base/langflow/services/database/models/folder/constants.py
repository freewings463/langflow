"""
模块名称：文件夹默认值常量

本模块提供文件夹名称与描述的默认常量。
主要功能包括：默认文件夹名、描述与历史名称列表。

关键组件：`DEFAULT_FOLDER_NAME` / `DEFAULT_FOLDER_DESCRIPTION` / `LEGACY_FOLDER_NAMES`
设计背景：统一默认文件夹命名与兼容旧版本名称。
使用场景：初始化用户目录或迁移旧数据。
注意事项：`DEFAULT_FOLDER_NAME` 可被环境变量覆盖。
"""

import os

DEFAULT_FOLDER_DESCRIPTION = "Manage your own flows. Download and upload projects."
# 注意：优先读取 `DEFAULT_FOLDER_NAME` 环境变量。
DEFAULT_FOLDER_NAME = os.getenv("DEFAULT_FOLDER_NAME", "Starter Project")

# 注意：历史版本可能存在的文件夹名称。
LEGACY_FOLDER_NAMES = ["My Collection", "Starter Project"]
