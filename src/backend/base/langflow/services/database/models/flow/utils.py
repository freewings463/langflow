"""
模块名称：`Flow` 工具函数

本模块提供流程数据的便捷查询方法。
主要功能包括：提取 `Webhook` 组件与识别组件版本差异。

关键组件：`get_webhook_component_in_flow` / `get_outdated_components`
设计背景：复用流程数据遍历逻辑，避免重复实现。
使用场景：流程校验、迁移提示与调试工具。
注意事项：基于节点 `id` 字符串包含 `Webhook` 进行判断。
"""

from langflow.utils.version import get_version_info

from .model import Flow


def get_webhook_component_in_flow(flow_data: dict):
    """返回首个 `Webhook` 组件节点。

    契约：
    - 输入：`flow_data` 字典。
    - 输出：匹配节点或 `None`。
    - 副作用：无。
    - 失败语义：输入缺失 `nodes` 时返回 `None`。

    关键路径：遍历 `nodes` 并查找 `id` 含 `Webhook` 的节点。

    决策：以 `id` 字符串包含关系识别 `Webhook`。
    问题：流程数据未提供统一类型字段。
    方案：使用 `id` 关键字匹配。
    代价：误匹配风险存在。
    重评：当节点类型字段稳定后改为类型判断。
    """
    if "nodes" in flow_data:
        for node in flow_data.get("nodes", []):
            if "Webhook" in node.get("id"):
                return node
    return None


def get_all_webhook_components_in_flow(flow_data: dict | None):
    """返回所有 `Webhook` 组件节点。

    契约：
    - 输入：`flow_data` 或 `None`。
    - 输出：节点列表（可为空）。
    - 副作用：无。
    - 失败语义：`flow_data` 为空时返回空列表。

    关键路径：过滤 `nodes` 中 `id` 含 `Webhook` 的节点。

    决策：保持返回列表以便调用方批量处理。
    问题：可能存在多个 `Webhook` 节点。
    方案：收集所有匹配节点并返回。
    代价：调用方需自行处理空列表。
    重评：当只允许单一 `Webhook` 时改为单节点返回。
    """
    if not flow_data:
        return []
    return [node for node in flow_data.get("nodes", []) if "Webhook" in node.get("id")]


def get_components_versions(flow: Flow):
    """提取流程内组件版本映射。

    契约：
    - 输入：`flow` 对象。
    - 输出：`{node_id: lf_version}` 字典。
    - 副作用：无。
    - 失败语义：`flow.data` 缺失时返回空字典。

    关键路径：
    1) 读取 `flow.data.nodes`。
    2) 提取 `data.node.lf_version`。

    决策：只返回包含 `lf_version` 的节点。
    问题：部分节点不包含版本字段。
    方案：跳过缺失字段节点。
    代价：版本缺失节点不参与差异检测。
    重评：当版本字段强制要求时改为抛错。
    """
    versions: dict[str, str] = {}
    if flow.data is None:
        return versions
    nodes = flow.data.get("nodes", [])
    for node in nodes:
        data = node.get("data", {})
        data_node = data.get("node", {})
        if "lf_version" in data_node:
            versions[node["id"]] = data_node["lf_version"]
    return versions


def get_outdated_components(flow: Flow):
    """识别版本过期的组件节点。

    契约：
    - 输入：`flow` 对象。
    - 输出：过期组件 `id` 列表。
    - 副作用：读取当前版本信息。
    - 失败语义：版本信息缺失时可能抛异常。

    关键路径：
    1) 读取组件版本映射。
    2) 比较当前 `Langflow` 版本。
    3) 收集不一致节点。

    决策：以 `lf_version` 与当前版本不一致为过期判断。
    问题：组件版本与系统版本不同可能导致兼容性问题。
    方案：简单比较字符串版本号。
    代价：忽略语义化版本的兼容范围。
    重评：当引入语义化比较时改为版本解析。
    """
    component_versions = get_components_versions(flow)
    lf_version = get_version_info()["version"]
    outdated_components = []
    for key, value in component_versions.items():
        if value != lf_version:
            outdated_components.append(key)
    return outdated_components
