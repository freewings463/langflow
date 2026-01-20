"""
模块名称：DirectoryReader 导出入口

本模块提供目录读取器的统一导出入口，便于上层模块引用。
主要功能：
- 对外导出 `DirectoryReader`。

设计背景：统一导入路径，减少调用方与具体文件的耦合。
注意事项：新增导出对象时需同步更新 `__all__`。
"""

from .directory_reader import DirectoryReader

__all__ = ["DirectoryReader"]
