"""
模块名称：接口层包入口

本模块提供 interface 子包的统一导出，主要用于集中暴露组件加载、初始化与运行辅助能力。主要功能包括：
- 聚合 interface 子模块的可用接口
- 提供稳定的包级导入路径

关键组件：
- `components`：组件索引与加载策略
- `initialize`：组件实例化与参数加载
- `utils`：接口层通用工具函数

设计背景：统一接口层入口，降低上层模块的导入复杂度。
使用场景：服务启动、组件加载、运行时辅助工具调用。
注意事项：当前为直接导入，子模块初始化可能触发依赖加载。
"""

# 注意：集中导入子模块以提供统一入口。
from . import components, importing, initialize, listing, run, utils

__all__ = ["components", "importing", "initialize", "listing", "run", "utils"]
