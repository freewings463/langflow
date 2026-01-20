"""
模块名称：payload

本模块提供负载处理相关的实用函数，主要用于处理模板和节点数据。
主要功能包括：
- 从模板中提取输入变量
- 构建JSON数据结构
- 获取图的根节点

设计背景：在处理模板和节点数据时，需要提取和构建各种数据结构
注意事项：使用contextlib.suppress来抑制异常，保证处理流程的连续性
"""

import contextlib
import re


def extract_input_variables(nodes):
    """从模板中提取输入变量并将其添加到input_variables字段。
    
    关键路径（三步）：
    1) 遍历所有节点
    2) 根据节点类型（prompt或few_shot）使用正则表达式提取变量
    3) 将提取的变量赋值给input_variables的value字段
    
    异常流：所有异常都被抑制，确保处理继续
    性能瓶颈：正则表达式的执行
    排障入口：检查返回的节点是否包含正确的输入变量
    """
    for node in nodes:
        with contextlib.suppress(Exception):
            if "input_variables" in node["data"]["node"]["template"]:
                if node["data"]["node"]["template"]["_type"] == "prompt":
                    variables = re.findall(
                        r"\{(.*?)\}",
                        node["data"]["node"]["template"]["template"]["value"],
                    )
                elif node["data"]["node"]["template"]["_type"] == "few_shot":
                    variables = re.findall(
                        r"\{(.*?)\}",
                        node["data"]["node"]["template"]["prefix"]["value"]
                        + node["data"]["node"]["template"]["suffix"]["value"],
                    )
                else:
                    variables = []
                node["data"]["node"]["template"]["input_variables"]["value"] = variables
    return nodes


def get_root_vertex(graph):
    """返回模板的根节点。
    
    关键路径（三步）：
    1) 收集所有边的源节点ID
    2) 如果没有入边且只有一个顶点，则返回该顶点
    3) 否则返回不在入边源节点集合中的顶点
    
    异常流：如果没有找到根节点则返回None
    性能瓶颈：图遍历操作
    排障入口：检查返回的节点是否确实是根节点
    """
    incoming_edges = {edge.source_id for edge in graph.edges}

    if not incoming_edges and len(graph.vertices) == 1:
        return graph.vertices[0]

    return next((node for node in graph.vertices if node.id not in incoming_edges), None)


def build_json(root, graph) -> dict:
    """从根节点和图构建JSON字典。
    
    关键路径（三步）：
    1) 确定当前节点的局部子节点
    2) 递归构建子节点的JSON结构
    3) 合并所有值到最终字典
    
    异常流：如果找不到指定类型的子节点会抛出ValueError
    性能瓶颈：递归遍历图结构
    排障入口：检查返回的字典结构是否符合预期
    """
    if "node" not in root.data:
        # If the root node has no "node" key, then it has only one child,
        # which is the target of the single outgoing edge
        edge = root.edges[0]
        local_nodes = [edge.target]
    else:
        # Otherwise, find all children whose type matches the type
        # specified in the template
        node_type = root.node_type
        local_nodes = graph.get_nodes_with_target(root)

    if len(local_nodes) == 1:
        return build_json(local_nodes[0], graph)
    # Build a dictionary from the template
    template = root.data["node"]["template"]
    final_dict = template.copy()

    for key in final_dict:
        if key == "_type":
            continue

        value = final_dict[key]
        node_type = value["type"]

        if "value" in value and value["value"] is not None:
            # If the value is specified, use it
            value = value["value"]
        elif "dict" in node_type:
            # If the value is a dictionary, create an empty dictionary
            value = {}
        else:
            # Otherwise, recursively build the child nodes
            children = []
            for local_node in local_nodes:
                node_children = graph.get_children_by_node_type(local_node, node_type)
                children.extend(node_children)

            if value["required"] and not children:
                msg = f"No child with type {node_type} found"
                raise ValueError(msg)
            values = [build_json(child, graph) for child in children]
            value = (
                list(values) if value["list"] else next(iter(values), None)  # type: ignore[arg-type]
            )
        final_dict[key] = value

    return final_dict
