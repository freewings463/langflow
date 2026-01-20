"""
模块名称：Vertex 异常定义

模块目的：集中定义 Vertex 相关运行时异常。
使用场景：组件实例缺失或状态异常时抛出明确错误。
主要功能包括：
- `NoComponentInstanceError`：组件实例不存在

设计背景：为上层提供可识别、可捕获的异常类型。
注意：异常信息应保持稳定，便于日志检索与排障。
"""

class NoComponentInstanceError(Exception):
    """组件实例缺失异常。

    契约：传入 `vertex_id` 生成可读错误信息。
    失败语义：用于指示节点尚未绑定组件实例。
    排障：检查节点是否完成初始化与 `instantiate_component` 调用。
    """
    def __init__(self, vertex_id: str):
        message = f"Vertex {vertex_id} does not have a component instance."
        super().__init__(message)
