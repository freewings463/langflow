"""
模块名称：流程图可视化与摘要

本模块将流程数据转换为可阅读的文本/`ASCII` 图形，便于在无 `UI` 环境下快速理解拓扑结构。主要功能包括：
- 图形表示：生成 `ASCII` 图与 `repr` 文本
- 图摘要：返回顶点/边数量与 `ID` 列表

关键组件：
- `get_flow_graph_representations`：统一输出 `ASCII` 与文本表示
- `get_flow_ascii_graph` / `get_flow_text_repr`：便捷字符串接口
- `get_flow_graph_summary`：轻量级摘要

设计背景：`CLI`/`Agentic` 场景需要快速排障与拓扑核对。
注意事项：`ASCII` 生成可能失败（复杂或有环），会返回提示字符串。
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from lfx.graph.graph.ascii import draw_graph
from lfx.graph.graph.base import Graph
from lfx.log.logger import logger

from langflow.helpers.flow import get_flow_by_id_or_endpoint_name

if TYPE_CHECKING:
    from langflow.services.database.models.flow.model import FlowRead


async def get_flow_graph_representations(
    flow_id_or_name: str,
    user_id: str | UUID | None = None,
) -> dict[str, Any]:
    """返回流程的 `ASCII` 与文本表示，便于排障与快速查看结构。

    契约：返回 `ascii_graph`/`text_repr`/`vertex_count`/`edge_count` 等字段。
    副作用：构建 `Graph` 并记录日志 `Error getting flow graph representations`。
    失败语义：流程不存在或无数据时返回含 `error` 的字典。
    关键路径（三步）：1) 载入流程 2) 构建图并生成 `repr` 3) 尝试 `ASCII` 渲染
    异常流：`ASCII` 渲染异常 -> 写入 `Failed to generate ASCII graph` 并返回提示字符串。
    性能瓶颈：`Graph.from_payload` 与 `ASCII` 渲染均随节点/边增长。
    排障入口：日志关键字 `Error getting flow graph representations`。
    决策：仅在存在顶点与边时生成 `ASCII`
    问题：无边或空图时渲染器返回无意义图形
    方案：检查 `vertices` 与 `edges` 均非空后再调用 `draw_graph`
    代价：对空图只返回 `None` 而非结构化 `ASCII`
    重评：当需要展示孤立节点时
    """
    try:
        flow: FlowRead | None = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

        if flow is None:
            return {
                "error": f"Flow {flow_id_or_name} not found",
                "flow_id": flow_id_or_name,
            }

        if flow.data is None:
            return {
                "error": f"Flow {flow_id_or_name} has no data",
                "flow_id": str(flow.id),
                "flow_name": flow.name,
            }

        flow_id_str = str(flow.id)
        graph = Graph.from_payload(
            flow.data,
            flow_id=flow_id_str,
            flow_name=flow.name,
        )

        text_repr = repr(graph)

        vertices = [vertex.id for vertex in graph.vertices]
        edges = [(edge.source_id, edge.target_id) for edge in graph.edges]

        ascii_graph = None
        if vertices and edges:
            try:
                ascii_graph = draw_graph(vertices, edges, return_ascii=True)
            except Exception as e:  # noqa: BLE001
                await logger.awarning(f"Failed to generate ASCII graph: {e}")
                ascii_graph = "ASCII graph generation failed (graph may be too complex or have cycles)"

        return {
            "flow_id": flow_id_str,
            "flow_name": flow.name,
            "ascii_graph": ascii_graph,
            "text_repr": text_repr,
            "vertex_count": len(graph.vertices),
            "edge_count": len(graph.edges),
            "tags": flow.tags,
            "description": flow.description,
        }

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting flow graph representations for {flow_id_or_name}: {e}")
        return {
            "error": str(e),
            "flow_id": flow_id_or_name,
        }

    finally:
        await logger.ainfo("Getting flow graph representations completed")


async def get_flow_ascii_graph(
    flow_id_or_name: str,
    user_id: str | UUID | None = None,
) -> str:
    """返回 `ASCII` 图字符串，失败时返回 `Error: <msg>`。

    契约：始终返回字符串；错误时带 `Error:` 前缀。
    副作用：复用 `get_flow_graph_representations` 的日志行为。
    失败语义：当底层返回 `error` 时拼接为 `Error: ...`。
    性能瓶颈：取决于底层图构建与 `ASCII` 渲染。
    排障入口：日志关键字 `Error getting flow graph representations`。
    决策：将错误包装为字符串而非抛异常
    问题：`CLI`/`Agentic` 调用期望直接可打印的结果
    方案：统一使用 `Error:` 前缀输出
    代价：调用方需解析字符串判断失败
    重评：当需要结构化错误或多语言提示时
    """
    result = await get_flow_graph_representations(flow_id_or_name, user_id)
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("ascii_graph") or "No ASCII graph available"


async def get_flow_text_repr(
    flow_id_or_name: str,
    user_id: str | UUID | None = None,
) -> str:
    """返回 `repr(graph)` 字符串，失败时返回 `Error: <msg>`。

    契约：始终返回字符串；错误时带 `Error:` 前缀。
    副作用：复用 `get_flow_graph_representations` 的日志行为。
    失败语义：当底层返回 `error` 时拼接为 `Error: ...`。
    性能瓶颈：取决于底层图构建。
    排障入口：日志关键字 `Error getting flow graph representations`。
    决策：统一返回字符串以便 `CLI` 直出
    问题：调用方多为文本输出场景
    方案：错误时也返回字符串而非异常
    代价：调用方需自行解析错误
    重评：当需要机器可读结构时
    """
    result = await get_flow_graph_representations(flow_id_or_name, user_id)
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("text_repr") or "No text representation available"


async def get_flow_graph_summary(
    flow_id_or_name: str,
    user_id: str | UUID | None = None,
) -> dict[str, Any]:
    """返回流程图的轻量级摘要，不生成 `ASCII` 或文本表示。

    契约：输出 `vertex_count`/`edge_count`/`vertices`/`edges` 等摘要字段。
    副作用：构建 `Graph` 并记录日志 `Error getting flow graph summary`。
    失败语义：流程不存在或无数据时返回含 `error` 的字典。
    关键路径（三步）：1) 载入流程 2) 构建图 3) 汇总顶点/边
    异常流：构建图异常 -> 返回 `error` 字段。
    性能瓶颈：构图成本与节点/边数量线性相关。
    排障入口：日志关键字 `Error getting flow graph summary`。
    决策：不复用 `ASCII`/文本生成以降低开销
    问题：多数场景只需统计与 `ID` 列表
    方案：仅构图并提取集合信息
    代价：缺少可视化输出
    重评：当摘要仍不足以排障时
    """
    try:
        flow: FlowRead | None = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

        if flow is None:
            return {"error": f"Flow {flow_id_or_name} not found"}

        if flow.data is None:
            return {
                "error": f"Flow {flow_id_or_name} has no data",
                "flow_id": str(flow.id),
                "flow_name": flow.name,
            }

        flow_id_str = str(flow.id)
        graph = Graph.from_payload(flow.data, flow_id=flow_id_str, flow_name=flow.name)

        return {
            "flow_id": flow_id_str,
            "flow_name": flow.name,
            "vertex_count": len(graph.vertices),
            "edge_count": len(graph.edges),
            "vertices": [vertex.id for vertex in graph.vertices],
            "edges": [(edge.source_id, edge.target_id) for edge in graph.edges],
            "tags": flow.tags,
            "description": flow.description,
        }

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting flow graph summary for {flow_id_or_name}: {e}")
        return {"error": str(e)}
