"""
模块名称：data 包入口

本模块用于导出基础数据处理组件的公共入口，避免外部依赖直接穿透到子模块。
主要功能包括：
- 功能1：统一暴露 `BaseFileComponent` 作为文件类组件的基类。

使用场景：外部模块需要按稳定路径导入文件处理基类时。
关键组件：
- 类 `BaseFileComponent`：文件解析与数据装载的核心基类。

设计背景：对外只暴露稳定 API，降低内部模块重构的影响面。
注意事项：新增导出项需同步更新 `__all__` 以保证可发现性。
"""

from .base_file import BaseFileComponent

__all__ = [
    "BaseFileComponent",
]
