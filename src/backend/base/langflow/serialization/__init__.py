"""
模块名称：序列化入口导出

本模块对外暴露序列化主入口，保持 `langflow.serialization` 的稳定导入路径。
设计背景：序列化实现集中于 `serialization.py`，入口模块负责最小导出。
注意事项：仅导出函数，不应在此引入副作用。
"""

from .serialization import serialize

# 注意：显式导出用于稳定对外 `API`，新增入口需同步更新。
__all__ = ["serialize"]
