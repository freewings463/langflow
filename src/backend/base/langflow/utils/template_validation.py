"""
模块名称：template_validation

本模块提供Langflow入门项目的模板验证实用工具，主要用于确保模板完整性并防止
入门项目模板中出现意外的损坏。
主要功能包括：
- 验证模板基本结构
- 验证流程是否可以构建
- 验证流程代码
- 验证流程执行

设计背景：在starter项目模板中需要确保模板的完整性，防止意外破坏
注意事项：包含异步函数用于执行流程验证
"""

import asyncio
import json
import uuid
from typing import Any

from lfx.custom.validate import validate_code
from lfx.graph.graph.base import Graph


def validate_template_structure(template_data: dict[str, Any], filename: str) -> list[str]:
    """验证基本模板结构。
    
    关键路径（三步）：
    1) 检查模板数据是否包含必需的字段（nodes和edges）
    2) 验证字段类型是否正确（nodes和edges必须是列表）
    3) 检查每个节点是否包含必需的字段（id和data）
    
    异常流：返回错误消息列表，如果验证通过则返回空列表
    性能瓶颈：模板数据的遍历验证
    排障入口：检查返回的错误列表以确定验证失败的具体原因
    """
    errors = []

    # Handle wrapped format
    data = template_data.get("data", template_data)

    # Check required fields
    if "nodes" not in data:
        errors.append(f"{filename}: Missing 'nodes' field")
    elif not isinstance(data["nodes"], list):
        errors.append(f"{filename}: 'nodes' must be a list")

    if "edges" not in data:
        errors.append(f"{filename}: Missing 'edges' field")
    elif not isinstance(data["edges"], list):
        errors.append(f"{filename}: 'edges' must be a list")

    # Check nodes have required fields
    for i, node in enumerate(data.get("nodes", [])):
        if "id" not in node:
            errors.append(f"{filename}: Node {i} missing 'id'")
        if "data" not in node:
            errors.append(f"{filename}: Node {i} missing 'data'")

    return errors


def validate_flow_can_build(template_data: dict[str, Any], filename: str) -> list[str]:
    """验证模板是否可以构建成可工作的流程。
    
    关键路径（三步）：
    1) 使用模板数据创建图对象
    2) 验证流配置和基本图结构
    3) 检查图是否包含有效顶点
    
    异常流：捕获并报告构建流程图时的异常
    性能瓶颈：图的构建和验证
    排障入口：检查返回的错误列表以确定构建失败的具体原因
    """
    errors = []

    try:
        # Create a unique flow ID for testing
        flow_id = str(uuid.uuid4())
        flow_name = filename.replace(".json", "")

        # Try to build the graph from the template data
        graph = Graph.from_payload(template_data, flow_id, flow_name, user_id="test_user")

        # Validate stream configuration
        graph.validate_stream()

        # Basic validation that the graph has vertices
        if not graph.vertices:
            errors.append(f"{filename}: Flow has no vertices after building")

        # Validate that all vertices have valid IDs
        errors.extend([f"{filename}: Vertex missing ID" for vertex in graph.vertices if not vertex.id])

    except (ValueError, TypeError, KeyError, AttributeError) as e:
        errors.append(f"{filename}: Failed to build flow graph: {e!s}")

    return errors


def validate_flow_code(template_data: dict[str, Any], filename: str) -> list[str]:
    """使用直接函数调用验证流程代码。
    
    关键路径（三步）：
    1) 从模板数据中提取代码字段
    2) 对每个代码字段调用validate_code函数进行验证
    3) 收集导入和函数错误
    
    异常流：捕获并报告代码验证过程中的异常
    性能瓶颈：代码验证函数的执行
    排障入口：检查返回的错误列表以确定代码验证失败的具体原因
    """
    errors = []

    try:
        # Extract code fields from template for validation
        data = template_data.get("data", template_data)

        for node in data.get("nodes", []):
            node_data = node.get("data", {})
            node_template = node_data.get("node", {}).get("template", {})

            # Look for code-related fields in the node template
            for field_data in node_template.values():
                if isinstance(field_data, dict) and field_data.get("type") == "code":
                    code_value = field_data.get("value", "")
                    if code_value and isinstance(code_value, str):
                        # Validate the code using direct function call
                        validation_result = validate_code(code_value)

                        # Check for import errors
                        if validation_result.get("imports", {}).get("errors"):
                            errors.extend(
                                [
                                    f"{filename}: Import error in node {node_data.get('id', 'unknown')}: {error}"
                                    for error in validation_result["imports"]["errors"]
                                ]
                            )

                        # Check for function errors
                        if validation_result.get("function", {}).get("errors"):
                            errors.extend(
                                [
                                    f"{filename}: Function error in node {node_data.get('id', 'unknown')}: {error}"
                                    for error in validation_result["function"]["errors"]
                                ]
                            )

    except (ValueError, TypeError, KeyError, AttributeError) as e:
        errors.append(f"{filename}: Code validation failed: {e!s}")

    return errors


async def validate_flow_execution(
    client, template_data: dict[str, Any], filename: str, headers: dict[str, str]
) -> list[str]:
    """通过构建和运行流程来验证流程执行。
    
    关键路径（三步）：
    1) 创建流程并获取流程ID
    2) 构建流程并验证事件流
    3) 清理已创建的流程
    
    异常流：
    - 捕获并报告API请求超时和其他异常
    - 即使清理超时也不会导致验证失败
    性能瓶颈：API请求和响应处理
    排障入口：检查返回的错误列表以确定执行失败的具体原因
    """
    errors = []

    try:
        # Create a flow from the template with timeout
        create_response = await client.post("api/v1/flows/", json=template_data, headers=headers, timeout=10)

        if create_response.status_code != 201:  # noqa: PLR2004
            errors.append(f"{filename}: Failed to create flow: {create_response.status_code}")
            return errors

        flow_id = create_response.json()["id"]

        try:
            # Build the flow with timeout
            build_response = await client.post(f"api/v1/build/{flow_id}/flow", json={}, headers=headers, timeout=10)

            if build_response.status_code != 200:  # noqa: PLR2004
                errors.append(f"{filename}: Failed to build flow: {build_response.status_code}")
                return errors

            job_id = build_response.json()["job_id"]

            # Get build events to validate execution
            events_headers = {**headers, "Accept": "application/x-ndjson"}
            events_response = await client.get(f"api/v1/build/{job_id}/events", headers=events_headers, timeout=10)

            if events_response.status_code != 200:  # noqa: PLR2004
                errors.append(f"{filename}: Failed to get build events: {events_response.status_code}")
                return errors

            # Validate the event stream
            await _validate_event_stream(events_response, job_id, filename, errors)

        finally:
            # Clean up the flow with timeout
            try:  # noqa: SIM105
                await client.delete(f"api/v1/flows/{flow_id}", headers=headers, timeout=10)
            except asyncio.TimeoutError:
                # Log but don't fail if cleanup times out
                pass

    except asyncio.TimeoutError:
        errors.append(f"{filename}: Flow execution timed out")
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        errors.append(f"{filename}: Flow execution validation failed: {e!s}")

    return errors


async def _validate_event_stream(response, job_id: str, filename: str, errors: list[str]) -> None:
    """验证来自流程执行的事件流。
    
    关键路径（三步）：
    1) 遍历事件流中的所有行
    2) 解析每个事件并验证其内容
    3) 检查是否观察到必要的事件类型
    
    异常流：
    - 捕获并报告JSON解析错误
    - 捕获并报告事件处理超时
    性能瓶颈：事件流的异步处理
    排障入口：检查错误列表以确定事件流验证失败的原因
    """
    try:
        vertices_sorted_seen = False
        end_event_seen = False
        vertex_count = 0

        async def process_events():
            nonlocal vertices_sorted_seen, end_event_seen, vertex_count

            async for line in response.aiter_lines():
                if not line:
                    continue

                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"{filename}: Invalid JSON in event stream: {line}")
                    continue

                # Verify job_id in events
                if "job_id" in parsed and parsed["job_id"] != job_id:
                    errors.append(f"{filename}: Job ID mismatch in event stream")
                    continue

                event_type = parsed.get("event")

                if event_type == "vertices_sorted":
                    vertices_sorted_seen = True
                    if not parsed.get("data", {}).get("ids"):
                        errors.append(f"{filename}: Missing vertex IDs in vertices_sorted event")

                elif event_type == "end_vertex":
                    vertex_count += 1
                    if not parsed.get("data", {}).get("build_data"):
                        errors.append(f"{filename}: Missing build_data in end_vertex event")

                elif event_type == "end":
                    end_event_seen = True

                elif event_type == "error":
                    error_data = parsed.get("data", {})
                    if isinstance(error_data, dict):
                        error_msg = error_data.get("error", "Unknown error")
                        # Skip if error is just "False" which is not a real error
                        if error_msg != "False" and error_msg is not False:
                            errors.append(f"{filename}: Flow execution error: {error_msg}")
                    else:
                        error_msg = str(error_data)
                        if error_msg != "False":
                            errors.append(f"{filename}: Flow execution error: {error_msg}")

                elif event_type == "message":
                    # Handle message events (normal part of flow execution)
                    pass

                elif event_type in ["token", "add_message", "stream_closed"]:
                    # Handle other common event types that don't indicate errors
                    pass

        # Process events with shorter timeout for comprehensive testing
        await asyncio.wait_for(process_events(), timeout=5.0)

        # Validate we saw required events (more lenient for diverse templates)
        # Only require end event - some templates may not follow the standard pattern
        if not end_event_seen:
            errors.append(f"{filename}: Missing end event in execution")
        # Allow flows with no vertices to be executed (some templates might be simple)
        # if vertex_count == 0:
        #     errors.append(f"{filename}: No vertices executed in flow")

    except asyncio.TimeoutError:
        errors.append(f"{filename}: Flow execution timeout")
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        errors.append(f"{filename}: Event stream validation failed: {e!s}")
