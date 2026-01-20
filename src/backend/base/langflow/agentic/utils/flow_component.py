"""
模块名称：流程组件细节与字段操作

本模块为 `Agentic` `API` 提供“按流程定位组件并读取/更新字段”的能力，主要用于调试、可视化与自动化修改。主要功能包括：
- 组件详情读取：从流程图构建顶点并返回模板/输出等信息
- 字段读取与列举：按字段名读取或一次性列出全部字段
- 字段更新：写回数据库并进行用户权限校验

关键组件：
- `get_component_details`：返回组件节点详情与输入连线
- `get_component_field_value` / `list_component_fields`：字段读取
- `update_component_field_value`：持久化字段更新

设计背景：前端与自动化需要在不执行流程的情况下读取/修改节点配置。
注意事项：更新路径会写入数据库；权限校验仅依赖 `user_id` 比对。
"""

from typing import Any
from uuid import UUID

from lfx.graph.graph.base import Graph
from lfx.log.logger import logger

from langflow.helpers.flow import get_flow_by_id_or_endpoint_name
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import session_scope


async def get_component_details(
    flow_id_or_name: str,
    component_id: str,
    user_id: str | UUID | None = None,
) -> dict[str, Any]:
    """按流程与组件 `ID` 返回节点详情，包含模板与输入连线。

    契约：输入 `flow_id_or_name`/`component_id`；输出字典包含 `component_id`、`template`、`outputs` 等。
    副作用：读取流程数据并构建 `Graph`；异常时写入日志 `Error getting component details`。
    失败语义：未找到流程/组件或无数据时返回含 `error` 的字典。
    关键路径（三步）：1) 载入流程 2) 构建图并定位顶点 3) 序列化节点与连线数据
    异常流：构建图或序列化异常 -> 返回 `error` 字段并记录日志。
    性能瓶颈：`Graph.from_payload` 会遍历完整流程数据，流程越大越慢。
    排障入口：日志关键字 `Error getting component details`。
    决策：仅序列化输入连线的必要字段
    问题：`Edge` 对象不可直接 `JSON` 序列化
    方案：导出 `source/target/type/id` 四个字段
    代价：丢失连线对象的非关键属性
    重评：当调用方需要更丰富的边信息时
    """
    try:
        flow = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

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
        graph = Graph.from_payload(flow.data, flow_id=flow_id_str, flow_name=flow.name)

        try:
            vertex = graph.get_vertex(component_id)
        except ValueError:
            return {
                "error": f"Component {component_id} not found in flow {flow_id_or_name}",
                "flow_id": flow_id_str,
                "flow_name": flow.name,
            }

        component_data = vertex.to_data()

        # 注意：Edge 对象不可直接序列化，仅导出稳定字段用于排障与展示
        def serialize_edges(edges):
            return [
                {
                    "source": getattr(e, "source", None),
                    "target": getattr(e, "target", None),
                    "type": getattr(e, "type", None),
                    "id": getattr(e, "id", None),
                }
                for e in edges
            ]

        return {
            "component_id": vertex.id,
            "node": component_data.get("data", {}).get("node", {}),
            "component_type": component_data.get("data", {}).get("node", {}).get("type"),
            "display_name": component_data.get("data", {}).get("node", {}).get("display_name"),
            "description": component_data.get("data", {}).get("node", {}).get("description"),
            "template": component_data.get("data", {}).get("node", {}).get("template", {}),
            "outputs": component_data.get("data", {}).get("node", {}).get("outputs", []),
            "input_flow": serialize_edges(vertex.edges),
            "flow_id": flow_id_str,
            "flow_name": flow.name,
        }

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting component details for {component_id} in {flow_id_or_name}: {e}")
        return {
            "error": str(e),
            "flow_id": flow_id_or_name,
            "component_id": component_id,
        }


async def get_component_field_value(
    flow_id_or_name: str,
    component_id: str,
    field_name: str,
    user_id: str | UUID | None = None,
) -> dict[str, Any]:
    """读取组件字段值并返回字段配置。

    契约：`field_name` 必须在模板中存在；返回包含 `value`/`field_type`/`display_name`。
    副作用：读取流程数据并构建 `Graph`；异常时写入日志 `Error getting field`。
    失败语义：流程/组件/字段不存在时返回含 `error` 的字典与可用字段列表。
    关键路径（三步）：1) 载入流程与图 2) 定位组件模板 3) 读取字段配置并返回
    异常流：图构建异常 -> 返回 `error` 字段。
    性能瓶颈：`Graph.from_payload` 随流程规模线性增长。
    排障入口：日志关键字 `Error getting field`。
    决策：`field_type` 优先 `field_type` 其次 `_input_type`
    问题：历史模板字段类型命名不一致
    方案：双字段兼容，提升模板向后兼容性
    代价：字段类型来源不唯一
    重评：当模板结构完成统一且弃用 `_input_type` 时
    """
    try:
        flow = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

        if flow is None:
            return {"error": f"Flow {flow_id_or_name} not found"}

        if flow.data is None:
            return {"error": f"Flow {flow_id_or_name} has no data"}

        flow_id_str = str(flow.id)
        graph = Graph.from_payload(flow.data, flow_id=flow_id_str, flow_name=flow.name)

        try:
            vertex = graph.get_vertex(component_id)
        except ValueError:
            return {
                "error": f"Component {component_id} not found in flow {flow_id_or_name}",
                "flow_id": flow_id_str,
            }

        component_data = vertex.to_data()
        template = component_data.get("data", {}).get("node", {}).get("template", {})

        if field_name not in template:
            available_fields = list(template.keys())
            return {
                "error": f"Field {field_name} not found in component {component_id}",
                "available_fields": available_fields,
                "component_id": component_id,
                "flow_id": flow_id_str,
            }

        field_config = template[field_name]

        return {
            "field_name": field_name,
            "value": field_config.get("value"),
            "field_type": field_config.get("field_type") or field_config.get("_input_type"),
            "display_name": field_config.get("display_name"),
            "required": field_config.get("required", False),
            "component_id": component_id,
            "flow_id": flow_id_str,
            **field_config,
        }

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting field {field_name} from {component_id} in {flow_id_or_name}: {e}")
        return {"error": str(e)}


async def update_component_field_value(
    flow_id_or_name: str,
    component_id: str,
    field_name: str,
    new_value: Any,
    user_id: str | UUID,
) -> dict[str, Any]:
    """更新组件字段并持久化到数据库。

    契约：`user_id` 必填且需与流程归属一致；返回包含 `success` 与新旧值。
    副作用：写入数据库并提交事务；失败时写入日志 `Error updating field`。
    失败语义：返回 `success=False` 与 `error`，不抛异常给调用方。
    关键路径（三步）：1) 载入流程数据 2) 就地更新模板字段 3) 校验权限并写回 `DB`
    异常流：数据库访问异常 -> 记录日志并返回失败。
    性能瓶颈：复制并写回完整 `flow.data`，大流程更新成本更高。
    排障入口：日志关键字 `Error updating field`。
    决策：失败返回 `success=False` 而非抛错
    问题：调用方多为 `UI`/自动化脚本，需统一错误结构
    方案：捕获异常并用结构化结果返回
    代价：调用方必须检查 `success`
    重评：当需要强一致错误传播或事务外层统一处理时
    """
    try:
        flow = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

        if flow is None:
            return {"error": f"Flow {flow_id_or_name} not found", "success": False}

        if flow.data is None:
            return {"error": f"Flow {flow_id_or_name} has no data", "success": False}

        flow_id_str = str(flow.id)

        flow_data = flow.data.copy()
        nodes = flow_data.get("nodes", [])

        component_found = False
        old_value = None

        for node in nodes:
            if node.get("id") == component_id:
                component_found = True
                template = node.get("data", {}).get("node", {}).get("template", {})

                if field_name not in template:
                    available_fields = list(template.keys())
                    return {
                        "error": f"Field {field_name} not found in component {component_id}",
                        "available_fields": available_fields,
                        "success": False,
                    }

                old_value = template[field_name].get("value")
                template[field_name]["value"] = new_value
                break

        if not component_found:
            return {
                "error": f"Component {component_id} not found in flow {flow_id_or_name}",
                "success": False,
            }

        async with session_scope() as session:
            db_flow = await session.get(Flow, UUID(flow_id_str))

            if not db_flow:
                return {"error": f"Flow {flow_id_str} not found in database", "success": False}

            if str(db_flow.user_id) != str(user_id):
                return {"error": "User does not have permission to update this flow", "success": False}

            db_flow.data = flow_data
            session.add(db_flow)
            await session.commit()
            await session.refresh(db_flow)

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error updating field {field_name} in {component_id} of {flow_id_or_name}: {e}")
        return {"error": str(e), "success": False}
    else:
        return {
            "success": True,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "component_id": component_id,
            "flow_id": flow_id_str,
            "flow_name": flow.name,
        }
    finally:
        await logger.ainfo("Updating field value completed")


async def list_component_fields(
    flow_id_or_name: str,
    component_id: str,
    user_id: str | UUID | None = None,
) -> dict[str, Any]:
    """列出组件模板中的全部字段及其当前值。

    契约：返回 `fields` 为 `field_name -> field_info` 映射，并附 `field_count`。
    副作用：读取流程数据并构建 `Graph`；异常时写入日志 `Error listing fields`。
    失败语义：流程/组件不存在时返回含 `error` 的字典。
    关键路径（三步）：1) 载入流程与图 2) 读取模板字段 3) 归一化字段信息输出
    异常流：图构建异常 -> 返回 `error` 字段。
    性能瓶颈：字段数量线性增长，字段多时返回体积较大。
    排障入口：日志关键字 `Error listing fields`。
    决策：只返回常用字段子集而非完整模板
    问题：完整模板包含 `UI`/内部字段，冗余且体积大
    方案：挑选 `value`/`field_type`/`display_name` 等关键字段
    代价：调用方无法直接获得所有原始配置
    重评：当下游需要更完整配置或调试诉求增加时
    """
    try:
        flow = await get_flow_by_id_or_endpoint_name(flow_id_or_name, user_id)

        if flow is None:
            return {"error": f"Flow {flow_id_or_name} not found"}

        if flow.data is None:
            return {"error": f"Flow {flow_id_or_name} has no data"}

        flow_id_str = str(flow.id)
        graph = Graph.from_payload(flow.data, flow_id=flow_id_str, flow_name=flow.name)

        try:
            vertex = graph.get_vertex(component_id)
        except ValueError:
            return {
                "error": f"Component {component_id} not found in flow {flow_id_or_name}",
                "flow_id": flow_id_str,
            }

        component_data = vertex.to_data()
        template = component_data.get("data", {}).get("node", {}).get("template", {})

        fields_info = {}
        for field_name, field_config in template.items():
            fields_info[field_name] = {
                "value": field_config.get("value"),
                "field_type": field_config.get("field_type") or field_config.get("_input_type"),
                "display_name": field_config.get("display_name"),
                "required": field_config.get("required", False),
                "advanced": field_config.get("advanced", False),
                "show": field_config.get("show", True),
            }

        return {
            "component_id": component_id,
            "component_type": component_data.get("data", {}).get("node", {}).get("type"),
            "display_name": component_data.get("data", {}).get("node", {}).get("display_name"),
            "flow_id": flow_id_str,
            "flow_name": flow.name,
            "fields": fields_info,
            "field_count": len(fields_info),
        }

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error listing fields for {component_id} in {flow_id_or_name}: {e}")
        return {"error": str(e)}
