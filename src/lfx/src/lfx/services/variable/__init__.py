"""
模块名称：Variable 服务导出入口

本模块对外导出变量服务，统一导入路径。
主要功能：
- 导出 VariableService。

设计背景：集中管理服务导入接口，减少调用方依赖细节。
注意事项：新增服务时需同步更新 `__all__`。
"""

from .service import VariableService

__all__ = ["VariableService"]
