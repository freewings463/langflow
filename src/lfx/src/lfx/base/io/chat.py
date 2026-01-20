"""
模块名称：基础聊天组件

本模块提供 ChatComponent，用于从上游模型组件提取展示信息与模型标识，
为聊天类节点提供统一的元信息获取路径。主要功能包括：
- 从入边组件读取模型字段
- 返回 (模型标识/图标/来源/组件ID) 元组

关键组件：ChatComponent、get_properties_from_source_component
设计背景：聊天节点需要统一的模型来源解析，避免各组件重复实现
注意事项：仅读取第一条入边；缺失时返回 (None, None, None, None)
"""

from lfx.custom.custom_component.component import Component


class ChatComponent(Component):
    """聊天类组件基类，提供从上游模型组件提取展示信息的能力。
    契约：输入为已连接的图节点；输出为上游模型标识与元信息元组；副作用为读取 graph/vertex。
    关键路径：定位入边 → 获取组件 → 解析模型字段 → 返回结果。
    决策：只取第一条入边。问题：多入边时模型选择不确定；方案：默认第一个；代价：忽略其它入边；重评：当支持多模型聚合时。
    """

    display_name = "Chat Component"
    description = "Use as base for chat components."

    def get_properties_from_source_component(self):
        """获取上游组件的模型标识与展示信息。
        契约：返回 (model_or_source, icon, source_name, component_id)；无入边时返回全 None。
        关键路径：取第一条入边 → 解析模型字段 → 回退展示名。
        决策：优先读取 `model_name`/`model_id`/`model`。问题：字段命名不统一；方案：按优先级探测；代价：多次属性访问；重评：当字段规范统一时。
        """
        vertex = self.get_vertex()
        if vertex and hasattr(vertex, "incoming_edges") and vertex.incoming_edges:
            source_id = vertex.incoming_edges[0].source_id
            source_vertex = self.graph.get_vertex(source_id)
            component = source_vertex.custom_component
            source = component.display_name
            icon = component.icon
            possible_attributes = ["model_name", "model_id", "model"]
            for attribute in possible_attributes:
                if hasattr(component, attribute) and getattr(component, attribute):
                    return getattr(component, attribute), icon, source, component.get_id()
            return source, icon, component.display_name, component.get_id()
        return None, None, None, None
