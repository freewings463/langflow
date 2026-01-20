"""模块名称：图状态模型生成

本模块基于图结构动态生成 Pydantic 状态模型，用于读取各顶点状态。
使用场景：在运行时按顶点访问状态，或为可视化/调试提供统一入口。
主要功能包括：
- 驼峰转蛇形命名
- 根据图顶点生成状态模型
"""

import re

from lfx.graph.state.model import create_state_model
from lfx.helpers.base_model import BaseModel


def camel_to_snake(camel_str: str) -> str:
    """将驼峰字符串转换为蛇形命名。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel_str).lower()


def create_state_model_from_graph(graph: BaseModel) -> type[BaseModel]:
    """从图结构生成 Pydantic 状态模型。

    契约：要求每个顶点具备 `custom_component` 且提供 getter
    关键路径：1) 校验顶点组件 2) 收集 getter 3) 生成动态模型
    异常流：缺少组件实例时抛 `ValueError`
    注意：顶点 ID 会被转换为蛇形字段名
    """
    for vertex in graph.vertices:
        if hasattr(vertex, "custom_component") and vertex.custom_component is None:
            msg = f"Vertex {vertex.id} does not have a component instance."
            raise ValueError(msg)

    state_model_getters = [
        vertex.custom_component.get_state_model_instance_getter()
        for vertex in graph.vertices
        if hasattr(vertex, "custom_component") and hasattr(vertex.custom_component, "get_state_model_instance_getter")
    ]
    fields = {
        camel_to_snake(vertex.id): state_model_getter
        for vertex, state_model_getter in zip(graph.vertices, state_model_getters, strict=False)
    }
    return create_state_model(model_name="GraphStateModel", validate=False, **fields)
