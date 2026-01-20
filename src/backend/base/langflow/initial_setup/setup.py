"""模块名称：初始配置与启动数据加载

模块目的：在启动与登录过程中初始化项目、文件夹与演示数据。
主要功能：
- `starter projects` 的加载与版本迁移
- `agentic flows` 的创建与更新
- 从文件系统/远程 `bundle` 导入 `flows`
使用场景：服务启动、用户首次登录、运维批量导入。
关键组件：`create_or_update_starter_projects`、`create_or_update_agentic_flows`、`load_bundles_from_urls`
设计背景：需要可重复执行的初始化流程并兼容历史数据结构。
注意事项：涉及数据库与文件 `I/O`，需在异步上下文与事务中调用。
"""

import asyncio
import copy
import io
import json
import re
import shutil
import zipfile
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AnyStr
from uuid import UUID

import anyio
import httpx
import orjson
import sqlalchemy as sa
from aiofile import async_open
from emoji import demojize, purely_emoji
from lfx.base.constants import (
    FIELD_FORMAT_ATTRIBUTES,
    NODE_FORMAT_ATTRIBUTES,
    ORJSON_OPTIONS,
    SKIPPED_COMPONENTS,
    SKIPPED_FIELD_ATTRIBUTES,
)
from lfx.log.logger import logger
from lfx.template.field.prompt import DEFAULT_PROMPT_INTUT_TYPES
from lfx.utils.util import escape_json_dump
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.initial_setup.constants import (
    ASSISTANT_FOLDER_DESCRIPTION,
    ASSISTANT_FOLDER_NAME,
    STARTER_FOLDER_DESCRIPTION,
    STARTER_FOLDER_NAME,
)
from langflow.services.auth.utils import create_super_user
from langflow.services.database.models.flow.model import Flow, FlowCreate
from langflow.services.database.models.folder.constants import (
    DEFAULT_FOLDER_DESCRIPTION,
    DEFAULT_FOLDER_NAME,
    LEGACY_FOLDER_NAMES,
)
from langflow.services.database.models.folder.model import Folder, FolderCreate, FolderRead
from langflow.services.deps import get_settings_service, get_storage_service, get_variable_service, session_scope

# 注意：`starter_projects` 目录存放示例项目 `JSON`，用于初始化数据库示例数据。


def update_projects_components_with_latest_component_versions(project_data, all_types_dict):
    """将项目节点模板更新为最新组件版本。

    关键路径：
    1) 扁平化组件索引并清理内部元数据
    2) 遍历节点并按最新模板/输出更新
    3) 记录变更日志并返回更新后的项目数据

    契约：仅更新已存在的节点类型；跳过 `SKIPPED_COMPONENTS`。
    失败语义：项目结构不完整时可能抛 `KeyError/TypeError`。
    """
    # 便于按 `node_type` 快速索引组件定义
    all_types_dict_flat = {}
    for category in all_types_dict.values():
        for key, component in category.items():
            # 注意：`hash_history` 仅用于组件索引追踪，不应写入保存的流程
            if "metadata" in component and "hash_history" in component["metadata"]:
                del component["metadata"]["hash_history"]
            all_types_dict_flat[key] = component

    node_changes_log = defaultdict(list)
    project_data_copy = deepcopy(project_data)

    for node in project_data_copy.get("nodes", []):
        node_data = node.get("data").get("node")
        node_type = node.get("data").get("type")

        if node_type in all_types_dict_flat:
            latest_node = all_types_dict_flat.get(node_type)
            latest_template = latest_node.get("template")
            node_data["template"]["code"] = latest_template["code"]
            # 注意：跳过需要持久化动态模板值的组件

            if node_type in SKIPPED_COMPONENTS:
                continue

            is_tool_or_agent = node_data.get("tool_mode", False) or node_data.get("key") in {
                "Agent",
                "LanguageModelComponent",
                "TypeConverterComponent",
            }
            has_tool_outputs = any(output.get("types") == ["Tool"] for output in node_data.get("outputs", []))
            if "outputs" in latest_node and not has_tool_outputs and not is_tool_or_agent:
                # 继承旧选中输出，避免 `UI` 状态丢失
                for output in latest_node["outputs"]:
                    node_data_output = next(
                        (output_ for output_ in node_data["outputs"] if output_["name"] == output["name"]),
                        None,
                    )
                    if node_data_output:
                        output["selected"] = node_data_output.get("selected")
                node_data["outputs"] = latest_node["outputs"]

            if node_data["template"]["_type"] != latest_template["_type"]:
                node_data["template"]["_type"] = latest_template["_type"]
                if node_type != "Prompt":
                    node_data["template"] = latest_template
                else:
                    for key, value in latest_template.items():
                        if key not in node_data["template"]:
                            node_changes_log[node_type].append(
                                {
                                    "attr": key,
                                    "old_value": None,
                                    "new_value": value,
                                }
                            )
                            node_data["template"][key] = value
                        elif isinstance(value, dict) and value.get("value"):
                            node_changes_log[node_type].append(
                                {
                                    "attr": key,
                                    "old_value": node_data["template"][key],
                                    "new_value": value,
                                }
                            )
                            node_data["template"][key]["value"] = value["value"]
                    for key in node_data["template"]:
                        if key not in latest_template:
                            node_data["template"][key]["input_types"] = DEFAULT_PROMPT_INTUT_TYPES
                node_changes_log[node_type].append(
                    {
                        "attr": "_type",
                        "old_value": node_data["template"]["_type"],
                        "new_value": latest_template["_type"],
                    }
                )
            else:
                for attr in NODE_FORMAT_ATTRIBUTES:
                    if (
                        attr in latest_node
                        # 仅在字段变化时写入
                        and latest_node[attr] != node_data.get(attr)
                    ):
                        node_changes_log[node_type].append(
                            {
                                "attr": attr,
                                "old_value": node_data.get(attr),
                                "new_value": latest_node[attr],
                            }
                        )
                        node_data[attr] = latest_node[attr]

                for field_name, field_dict in latest_template.items():
                    if field_name not in node_data["template"]:
                        node_data["template"][field_name] = field_dict
                        continue
                    # 仅更新允许覆盖的字段属性
                    to_check_attributes = FIELD_FORMAT_ATTRIBUTES
                    # 注意：跳过需要保留示例项目配置的属性（如 `advanced`）
                    # `SKIPPED_FIELD_ATTRIBUTES = {"advanced"}`
                    # 遍历可更新属性
                    for attr in to_check_attributes:
                        # 若属性在跳过集合中则保留原值
                        if attr in SKIPPED_FIELD_ATTRIBUTES:
                            continue
                        if (
                            attr in field_dict
                            and attr in node_data["template"].get(field_name)
                            # 仅在值变化时更新
                            and field_dict[attr] != node_data["template"][field_name][attr]
                        ):
                            node_changes_log[node_type].append(
                                {
                                    "attr": f"{field_name}.{attr}",
                                    "old_value": node_data["template"][field_name][attr],
                                    "new_value": field_dict[attr],
                                }
                            )
                            node_data["template"][field_name][attr] = field_dict[attr]
            # 清理最新模板中已不存在的字段
            if node_type != "Prompt":
                for field_name in list(node_data["template"].keys()):
                    is_tool_mode_and_field_is_tools_metadata = (
                        node_data.get("tool_mode", False) and field_name == "tools_metadata"
                    )
                    if field_name not in latest_template and not is_tool_mode_and_field_is_tools_metadata:
                        node_data["template"].pop(field_name)
    log_node_changes(node_changes_log)
    return project_data_copy


def scape_json_parse(json_string: str) -> dict:
    """将含转义占位符的 `JSON` 字符串解析为字典。

    契约：`None` 返回空字典；字典输入原样返回。
    失败语义：非法 `JSON` 字符串会抛 `json.JSONDecodeError`。
    """
    if json_string is None:
        return {}
    if isinstance(json_string, dict):
        return json_string
    parsed_string = json_string.replace("œ", '"')
    return json.loads(parsed_string)


def update_new_output(data):
    """修复旧格式输出与边的句柄信息。

    关键路径：
    1) 解析并标准化 source/target handle
    2) 补齐输出类型与节点输出列表
    3) 反写 edges 与 nodes 并返回拷贝

    契约：输入 `data` 需包含 `nodes`/`edges`。
    失败语义：结构缺失可能抛 `KeyError/TypeError`。
    """
    nodes = copy.deepcopy(data["nodes"])
    edges = copy.deepcopy(data["edges"])

    for edge in edges:
        if "sourceHandle" in edge and "targetHandle" in edge:
            new_source_handle = scape_json_parse(edge["sourceHandle"])
            new_target_handle = scape_json_parse(edge["targetHandle"])
            id_ = new_source_handle["id"]
            source_node_index = next((index for (index, d) in enumerate(nodes) if d["id"] == id_), -1)
            source_node = nodes[source_node_index] if source_node_index != -1 else None

            if "baseClasses" in new_source_handle:
                if "output_types" not in new_source_handle:
                    if source_node and "node" in source_node["data"] and "output_types" in source_node["data"]["node"]:
                        new_source_handle["output_types"] = source_node["data"]["node"]["output_types"]
                    else:
                        new_source_handle["output_types"] = new_source_handle["baseClasses"]
                del new_source_handle["baseClasses"]

            if new_target_handle.get("inputTypes"):
                intersection = [
                    type_ for type_ in new_source_handle["output_types"] if type_ in new_target_handle["inputTypes"]
                ]
            else:
                intersection = [
                    type_ for type_ in new_source_handle["output_types"] if type_ == new_target_handle["type"]
                ]

            selected = intersection[0] if intersection else None
            if "name" not in new_source_handle:
                new_source_handle["name"] = " | ".join(new_source_handle["output_types"])
            new_source_handle["output_types"] = [selected] if selected else []

            if source_node and not source_node["data"]["node"].get("outputs"):
                if "outputs" not in source_node["data"]["node"]:
                    source_node["data"]["node"]["outputs"] = []
                types = source_node["data"]["node"].get(
                    "output_types", source_node["data"]["node"].get("base_classes", [])
                )
                if not any(output.get("selected") == selected for output in source_node["data"]["node"]["outputs"]):
                    source_node["data"]["node"]["outputs"].append(
                        {
                            "types": types,
                            "selected": selected,
                            "name": " | ".join(types),
                            "display_name": " | ".join(types),
                        }
                    )
            deduplicated_outputs = []
            if source_node is None:
                source_node = {"data": {"node": {"outputs": []}}}

            for output in source_node["data"]["node"]["outputs"]:
                if output["name"] not in [d["name"] for d in deduplicated_outputs]:
                    deduplicated_outputs.append(output)
            source_node["data"]["node"]["outputs"] = deduplicated_outputs

            edge["sourceHandle"] = escape_json_dump(new_source_handle)
            edge["data"]["sourceHandle"] = new_source_handle
            edge["data"]["targetHandle"] = new_target_handle
    # 注意：部分 `sourceHandle` 缺失 `name`，需从节点输出补齐
    for node in nodes:
        if "outputs" in node["data"]["node"]:
            for output in node["data"]["node"]["outputs"]:
                for edge in edges:
                    if node["id"] != edge["source"] or output.get("method") is None:
                        continue
                    source_handle = scape_json_parse(edge["sourceHandle"])
                    if source_handle["output_types"] == output.get("types") and source_handle["name"] != output["name"]:
                        source_handle["name"] = output["name"]
                        if isinstance(source_handle, str):
                            source_handle = scape_json_parse(source_handle)
                        edge["sourceHandle"] = escape_json_dump(source_handle)
                        edge["data"]["sourceHandle"] = source_handle

    data_copy = copy.deepcopy(data)
    data_copy["nodes"] = nodes
    data_copy["edges"] = edges
    return data_copy


def update_edges_with_latest_component_versions(project_data):
    """更新边的句柄结构以匹配最新组件版本。

    关键路径：
    1) 深拷贝项目数据并解析 source/target handle
    2) 基于节点输出/模板更新 output_types 与 inputTypes
    3) 记录变更并回写 edge 数据

    契约：输入 `project_data` 需包含 `nodes` 与 `edges`。
    失败语义：节点缺失会记录错误日志并继续处理其他边。
    """
    # 用于记录边的变更，便于排障
    edge_changes_log = defaultdict(list)
    # 深拷贝避免污染原始数据
    project_data_copy = deepcopy(project_data)

    # 建立节点类型到节点 ID 的映射，便于缺失节点时尝试修复
    node_type_map = {}
    for node in project_data_copy.get("nodes", []):
        node_type = node.get("data", {}).get("type", "")
        if node_type:
            if node_type not in node_type_map:
                node_type_map[node_type] = []
            node_type_map[node_type].append(node.get("id"))

    # 遍历每条边并进行句柄更新
    for edge in project_data_copy.get("edges", []):
        # 解析源/目标句柄
        source_handle = edge.get("data", {}).get("sourceHandle")
        source_handle = scape_json_parse(source_handle)
        target_handle = edge.get("data", {}).get("targetHandle")
        target_handle = scape_json_parse(target_handle)

        # 查找源/目标节点
        source_node = next(
            (node for node in project_data.get("nodes", []) if node.get("id") == edge.get("source")),
            None,
        )
        target_node = next(
            (node for node in project_data.get("nodes", []) if node.get("id") == edge.get("target")),
            None,
        )

        # 若节点缺失，尝试按类型匹配修复
        if source_node is None and source_handle and "dataType" in source_handle:
            node_type = source_handle.get("dataType")
            if node_type_map.get(node_type):
                # 选择同类型第一个节点作为替代
                new_node_id = node_type_map[node_type][0]
                logger.info(f"Reconciling missing source node: replacing {edge.get('source')} with {new_node_id}")

                # 更新边的 `source`
                edge["source"] = new_node_id

                # 更新源句柄的 `id`
                source_handle["id"] = new_node_id

                # 获取替换后的源节点
                source_node = next(
                    (node for node in project_data.get("nodes", []) if node.get("id") == new_node_id),
                    None,
                )

                # 注意：`edge.id` 包含编码句柄，这里使用简化替换策略
                old_id_prefix = edge.get("id", "").split("{")[0]
                if old_id_prefix:
                    new_id_prefix = old_id_prefix.replace(edge.get("source"), new_node_id)
                    edge["id"] = edge.get("id", "").replace(old_id_prefix, new_id_prefix)

        if target_node is None and target_handle and "id" in target_handle:
            # 从目标句柄的 `id` 解析节点类型（示例：`AstraDBGraph-jr8pY` -> `AstraDBGraph`）
            id_parts = target_handle.get("id", "").split("-")
            if len(id_parts) > 0:
                node_type = id_parts[0]
                if node_type_map.get(node_type):
                    # 选择同类型第一个节点作为替代
                    new_node_id = node_type_map[node_type][0]
                    logger.info(f"Reconciling missing target node: replacing {edge.get('target')} with {new_node_id}")

                    # 更新边的 `target`
                    edge["target"] = new_node_id

                    # 更新目标句柄的 `id`
                    target_handle["id"] = new_node_id

                    # 获取替换后的目标节点
                    target_node = next(
                        (node for node in project_data.get("nodes", []) if node.get("id") == new_node_id),
                        None,
                    )

                    # 简化更新 `edge.id`
                    old_id_suffix = edge.get("id", "").split("}-")[1] if "}-" in edge.get("id", "") else ""
                    if old_id_suffix:
                        new_id_suffix = old_id_suffix.replace(edge.get("target"), new_node_id)
                        edge["id"] = edge.get("id", "").replace(old_id_suffix, new_id_suffix)

        if source_node and target_node:
            # 提前提取节点数据便于访问
            source_node_data = source_node.get("data", {}).get("node", {})
            target_node_data = target_node.get("data", {}).get("node", {})

            # 按源句柄的 `name` 匹配输出
            output_data = next(
                (
                    output
                    for output in source_node_data.get("outputs", [])
                    if output.get("name") == source_handle.get("name")
                ),
                None,
            )

            # 若未命中，尝试用 `display_name` 匹配
            if not output_data:
                output_data = next(
                    (
                        output
                        for output in source_node_data.get("outputs", [])
                        if output.get("display_name") == source_handle.get("name")
                    ),
                    None,
                )
            # 若通过 `display_name` 命中则同步 `name`
                if output_data:
                    source_handle["name"] = output_data.get("name")

            # 计算输出类型集合
            if output_data:
                if len(output_data.get("types", [])) == 1:
                    new_output_types = output_data.get("types", [])
                elif output_data.get("selected"):
                    new_output_types = [output_data.get("selected")]
                else:
                    new_output_types = []
            else:
                new_output_types = []

            # 若输出类型变化则记录并更新
            if source_handle.get("output_types", []) != new_output_types:
                edge_changes_log[source_node_data.get("display_name", "unknown")].append(
                    {
                        "attr": "output_types",
                        "old_value": source_handle.get("output_types", []),
                        "new_value": new_output_types,
                    }
                )
                source_handle["output_types"] = new_output_types

            # 若输入类型变化则记录并更新
            field_name = target_handle.get("fieldName")
            if field_name in target_node_data.get("template", {}) and target_handle.get(
                "inputTypes", []
            ) != target_node_data.get("template", {}).get(field_name, {}).get("input_types", []):
                edge_changes_log[target_node_data.get("display_name", "unknown")].append(
                    {
                        "attr": "inputTypes",
                        "old_value": target_handle.get("inputTypes", []),
                        "new_value": target_node_data.get("template", {}).get(field_name, {}).get("input_types", []),
                    }
                )
                target_handle["inputTypes"] = (
                    target_node_data.get("template", {}).get(field_name, {}).get("input_types", [])
                )

            # 将更新后的句柄转义后写回
            escaped_source_handle = escape_json_dump(source_handle)
            escaped_target_handle = escape_json_dump(target_handle)

            # 解析旧句柄用于对比
            try:
                old_escape_source_handle = escape_json_dump(json.loads(edge.get("sourceHandle", "{}")))
            except (json.JSONDecodeError, TypeError):
                old_escape_source_handle = edge.get("sourceHandle", "")

            try:
                old_escape_target_handle = escape_json_dump(json.loads(edge.get("targetHandle", "{}")))
            except (json.JSONDecodeError, TypeError):
                old_escape_target_handle = edge.get("targetHandle", "")

            # 更新源句柄并记录变更
            if old_escape_source_handle != escaped_source_handle:
                edge_changes_log[source_node_data.get("display_name", "unknown")].append(
                    {
                        "attr": "sourceHandle",
                        "old_value": old_escape_source_handle,
                        "new_value": escaped_source_handle,
                    }
                )
                edge["sourceHandle"] = escaped_source_handle
                if "data" in edge:
                    edge["data"]["sourceHandle"] = source_handle

            # 更新目标句柄并记录变更
            if old_escape_target_handle != escaped_target_handle:
                edge_changes_log[target_node_data.get("display_name", "unknown")].append(
                    {
                        "attr": "targetHandle",
                        "old_value": old_escape_target_handle,
                        "new_value": escaped_target_handle,
                    }
                )
                edge["targetHandle"] = escaped_target_handle
                if "data" in edge:
                    edge["data"]["targetHandle"] = target_handle

        else:
            # 排障：若修复后仍缺失节点，则记录错误
            logger.error(f"Source or target node not found for edge: {edge}")

    # 记录所有变更
    log_node_changes(edge_changes_log)
    return project_data_copy


def log_node_changes(node_changes_log) -> None:
    """将节点/边的变更日志格式化输出到 `debug`。

    契约：`node_changes_log` 为 `dict[str, list[dict]]`。
    """
    # 实现：按节点汇总变更并输出一条日志
    formatted_messages = []
    for node_name, changes in node_changes_log.items():
        message = f"\nNode: {node_name} was updated with the following changes:"
        for change in changes:
            message += f"\n- {change['attr']}: {change['old_value']} -> {change['new_value']}"
        formatted_messages.append(message)
    if formatted_messages:
        logger.debug("\n".join(formatted_messages))


async def load_starter_projects(retries=3, delay=1) -> list[tuple[anyio.Path, dict]]:
    """异步加载示例项目的 `JSON`。

    关键路径：
    1) 扫描 `starter_projects` 目录
    2) 逐文件解析 `JSON`，失败按重试策略处理
    3) 返回 `(path, data)` 列表

    契约：`retries` 为失败重试次数，`delay` 为重试间隔秒数。
    失败语义：重试耗尽后抛 `ValueError`。
    """
    starter_projects = []
    folder = anyio.Path(__file__).parent / "starter_projects"
    await logger.adebug("Loading starter projects")
    async for file in folder.glob("*.json"):
        attempt = 0
        while attempt < retries:
            async with async_open(str(file), "r", encoding="utf-8") as f:
                content = await f.read()
            try:
                project = orjson.loads(content)
                starter_projects.append((file, project))
                break  # 成功后退出重试
            except orjson.JSONDecodeError as e:
                attempt += 1
                if attempt >= retries:
                    msg = f"Error loading starter project {file}: {e}"
                    raise ValueError(msg) from e
                await asyncio.sleep(delay)  # 重试前等待
    await logger.adebug(f"Loaded {len(starter_projects)} starter projects")
    return starter_projects


async def copy_profile_pictures() -> None:
    """异步复制头像资源到配置目录。

    关键路径：
    1) 校验源目录并创建目标目录
    2) 读取目标目录已有文件以避免重复复制
    3) 并发复制文件并记录日志

    契约：源目录为 `profile_pictures`，按相对路径复制到配置目录。
    失败语义：配置目录未设置或复制异常时抛出错误。
    """
    # 从设置读取配置目录
    config_dir = get_storage_service().settings_service.settings.config_dir
    if config_dir is None:
        msg = "Config dir is not set in the settings"
        raise ValueError(msg)

    # 构建源/目标路径
    origin = anyio.Path(__file__).parent / "profile_pictures"
    target = anyio.Path(config_dir) / "profile_pictures"

    if not await origin.exists():
        msg = f"The source folder '{origin}' does not exist."
        raise ValueError(msg)

    # 目标目录不存在则创建
    if not await target.exists():
        await target.mkdir(parents=True, exist_ok=True)

    try:
        # 记录已存在文件，避免重复检查
        target_files = {str(f.relative_to(target)) async for f in target.rglob("*") if await f.is_file()}

        # 定义单文件复制任务
        async def copy_file(src_file, dst_file, rel_path):
            # 确保父目录存在
            await dst_file.parent.mkdir(parents=True, exist_ok=True)
            # 将阻塞 `I/O` 下放到线程
            await asyncio.to_thread(shutil.copy2, str(src_file), str(dst_file))
            await logger.adebug(f"Copied file '{rel_path}'")

        tasks = []
        async for src_file in origin.rglob("*"):
            if not await src_file.is_file():
                continue

            rel_path = src_file.relative_to(origin)
            if str(rel_path) not in target_files:
                dst_file = target / rel_path
                tasks.append(copy_file(src_file, dst_file, rel_path))

        if tasks:
            await asyncio.gather(*tasks)

    except Exception as exc:
        await logger.aexception("Error copying profile pictures")
        msg = "An error occurred while copying profile pictures."
        raise RuntimeError(msg) from exc


def get_project_data(project):
    """从项目字典中抽取标准化字段集合。"""
    project_name = project.get("name")
    project_description = project.get("description")
    project_is_component = project.get("is_component")
    project_updated_at = project.get("updated_at")
    if not project_updated_at:
        updated_at_datetime = datetime.now(tz=timezone.utc)
    else:
        updated_at_datetime = datetime.fromisoformat(project_updated_at)
    project_data = project.get("data")
    project_icon = project.get("icon")
    project_icon = demojize(project_icon) if project_icon and purely_emoji(project_icon) else project_icon
    project_icon_bg_color = project.get("icon_bg_color")
    project_gradient = project.get("gradient")
    project_tags = project.get("tags")
    return (
        project_name,
        project_description,
        project_is_component,
        updated_at_datetime,
        project_data,
        project_icon,
        project_icon_bg_color,
        project_gradient,
        project_tags,
    )


async def update_project_file(project_path: anyio.Path, project: dict, updated_project_data) -> None:
    """将更新后的项目数据回写到 `JSON` 文件。"""
    project["data"] = updated_project_data
    async with async_open(str(project_path), "w", encoding="utf-8") as f:
        await f.write(orjson.dumps(project, option=ORJSON_OPTIONS).decode())
    await logger.adebug(f"Updated starter project {project['name']} file")


def update_existing_project(
    existing_project,
    project_name,
    project_description,
    project_is_component,
    updated_at_datetime,
    project_data,
    project_icon,
    project_icon_bg_color,
) -> None:
    """将更新后的项目字段写回已有 `Flow` 记录。"""
    logger.info(f"Updating starter project {project_name}")
    existing_project.data = project_data
    existing_project.folder = STARTER_FOLDER_NAME
    existing_project.description = project_description
    existing_project.is_component = project_is_component
    existing_project.updated_at = updated_at_datetime
    existing_project.icon = project_icon
    existing_project.icon_bg_color = project_icon_bg_color


def create_new_project(
    session,
    project_name,
    project_description,
    project_is_component,
    updated_at_datetime,
    project_data,
    project_gradient,
    project_tags,
    project_icon,
    project_icon_bg_color,
    new_folder_id,
) -> None:
    """构建并写入新的示例项目记录。"""
    new_project = FlowCreate(
        name=project_name,
        description=project_description,
        icon=project_icon,
        icon_bg_color=project_icon_bg_color,
        data=project_data,
        is_component=project_is_component,
        updated_at=updated_at_datetime,
        folder_id=new_folder_id,
        gradient=project_gradient,
        tags=project_tags,
    )
    db_flow = Flow.model_validate(new_project, from_attributes=True)
    session.add(db_flow)


async def get_all_flows_similar_to_project(session: AsyncSession, folder_id: UUID) -> list[Flow]:
    """获取指定文件夹下的所有流程。"""
    stmt = select(Folder).options(selectinload(Folder.flows)).where(Folder.id == folder_id)
    return list((await session.exec(stmt)).first().flows)


async def delete_starter_projects(session, folder_id) -> None:
    """删除指定文件夹下的所有示例项目。"""
    flows = await get_all_flows_similar_to_project(session, folder_id)
    for flow in flows:
        await session.delete(flow)


async def folder_exists(session, folder_name):
    """判断指定名称的文件夹是否存在。"""
    stmt = select(Folder).where(Folder.name == folder_name)
    folder = (await session.exec(stmt)).first()
    return folder is not None


async def get_or_create_starter_folder(session):
    """获取或创建示例项目文件夹。"""
    if not await folder_exists(session, STARTER_FOLDER_NAME):
        new_folder = FolderCreate(name=STARTER_FOLDER_NAME, description=STARTER_FOLDER_DESCRIPTION)
        db_folder = Folder.model_validate(new_folder, from_attributes=True)
        session.add(db_folder)
        await session.flush()
        await session.refresh(db_folder)
        return db_folder
    stmt = select(Folder).where(Folder.name == STARTER_FOLDER_NAME)
    return (await session.exec(stmt)).first()


async def get_or_create_assistant_folder(session, user_id: UUID):
    """获取或创建 `Langflow Assistant` 文件夹。

    关键路径：
    1) 按 `user_id` 与文件夹名查询
    2) 不存在则创建并提交
    3) 返回文件夹记录

    契约：该文件夹用于存放 `agentic flows`，不应被删除。
    """
    stmt = select(Folder).where(Folder.user_id == user_id, Folder.name == ASSISTANT_FOLDER_NAME)
    result = await session.exec(stmt)
    folder = result.first()

    if not folder:
        new_folder = FolderCreate(name=ASSISTANT_FOLDER_NAME, description=ASSISTANT_FOLDER_DESCRIPTION)
        db_folder = Folder.model_validate(new_folder, from_attributes=True)
        db_folder.user_id = user_id
        session.add(db_folder)
        await session.commit()
        await session.refresh(db_folder)
        return db_folder
    return folder


async def load_agentic_flows() -> list[tuple[anyio.Path, dict]]:
    """从 `agentic/flows` 目录加载 `agentic flows`。

    关键路径：
    1) 校验目录存在
    2) 逐文件读取并解析 `JSON`
    3) 返回 `(path, flow)` 元组列表
    """
    agentic_flows: list[tuple[anyio.Path, dict]] = []
    # 目录位置：`agentic/flows`
    folder = anyio.Path(__file__).parent.parent / "agentic" / "flows"

    if not await folder.exists():
        await logger.adebug(f"Agentic flows directory does not exist: {folder}")
        return agentic_flows

    await logger.adebug("Loading agentic flows")
    async for file in folder.glob("*.json"):
        try:
            async with async_open(str(file), "r", encoding="utf-8") as f:
                content = await f.read()
            flow = orjson.loads(content)
            agentic_flows.append((file, flow))
            await logger.adebug(f"Loaded agentic flow: {file.name}")
        except (OSError, orjson.JSONDecodeError) as e:
            await logger.aexception(f"Error loading agentic flow {file}: {e}")

    await logger.adebug(f"Loaded {len(agentic_flows)} agentic flows")
    return agentic_flows


async def create_or_update_agentic_flows(session: AsyncSession, user_id: UUID) -> None:
    """在用户的 `Langflow Assistant` 文件夹中创建 `agentic flows`。

    关键路径：
    1) 检查 `agentic` 功能开关并获取文件夹
    2) 读取 `agentic flows` 的 `JSON` 并解析元数据
    3) 仅创建不存在的 `flows`（不更新已有）

    契约：仅在启用 `agentic_experience` 时执行。
    失败语义：解析或写库失败时记录异常日志并继续后续处理。
    """
    from lfx.services.deps import get_settings_service

    # 仅在启用 `agentic` 体验时执行
    settings_service = get_settings_service()
    if not settings_service.settings.agentic_experience:
        await logger.adebug("Agentic experience disabled, skipping agentic flows creation")
        return

    try:
        # 获取或创建助手文件夹
        assistant_folder = await get_or_create_assistant_folder(session, user_id)

        # 加载 `agentic flows`
        agentic_flows = await load_agentic_flows()

        if not agentic_flows:
            await logger.adebug("No agentic flows found to load")
            return

        flows_created = 0
        flows_updated = 0

        for _, flow_data in agentic_flows:
            # 从 `JSON` 提取流程元数据
            (
                flow_name,
                flow_description,
                flow_is_component,
                updated_at_datetime,
                project_data,
                flow_icon,
                flow_icon_bg_color,
                flow_gradient,
                flow_tags,
            ) = get_project_data(flow_data)

            # 提取 `flow_id` 与 `endpoint_name`
            flow_id = flow_data.get("id")
            flow_endpoint_name = flow_data.get("endpoint_name")

            # 将 `flow_id` 转换为 `UUID`（若合法）
            if flow_id and isinstance(flow_id, str):
                try:
                    flow_id = UUID(flow_id)
                except ValueError:
                    await logger.awarning(f"Invalid UUID for flow {flow_name}: {flow_id}, will use auto-generated ID")
                    flow_id = None

            # 按 `id` 或 `endpoint_name` 查找已存在流程
            existing_flow = await find_existing_flow(session, flow_id, flow_endpoint_name)

            if existing_flow:
                # 已存在则跳过更新
                await logger.adebug(f"Agentic flow already exists, skipping: {flow_name}")
                flows_updated += 1
            else:
                try:
                    await logger.adebug(f"Creating agentic flow: {flow_name}")
                    # 使用 `JSON` 中的 `id`/`endpoint_name` 创建流程
                    new_project = FlowCreate(
                        name=flow_name,
                        description=flow_description,
                        icon=flow_icon,
                        icon_bg_color=flow_icon_bg_color,
                        data=project_data,
                        is_component=flow_is_component,
                        updated_at=updated_at_datetime,
                        folder_id=assistant_folder.id,
                        gradient=flow_gradient,
                        tags=flow_tags,
                        endpoint_name=flow_endpoint_name,  # 使用 `JSON` 中的 `endpoint_name`
                    )
                    db_flow = Flow.model_validate(new_project, from_attributes=True)

                    # 若 `JSON` 提供 `ID` 则直接赋值
                    if flow_id:
                        db_flow.id = flow_id

                    session.add(db_flow)
                    flows_created += 1
                except Exception:  # noqa: BLE001
                    await logger.aexception(f"Error while creating agentic flow {flow_name}")

        if flows_created > 0 or flows_updated > 0:
            await session.commit()
            await logger.adebug(
                f"Successfully created {flows_created} and skipped {flows_updated} existing agentic flows"
            )
        else:
            await logger.adebug("No agentic flows to create")

    except Exception:  # noqa: BLE001
        await logger.aexception("Error in create_or_update_agentic_flows")


def _is_valid_uuid(val):
    """校验字符串是否为合法 `UUID`。"""
    try:
        uuid_obj = UUID(val)
    except ValueError:
        return False
    return str(uuid_obj) == val


async def load_flows_from_directory() -> None:
    """启动时从配置目录加载流程并写入默认文件夹。

    关键路径：
    1) 读取 `load_flows_path` 并遍历 `JSON`
    2) 查询超级用户并确保默认文件夹存在
    3) 逐文件 upsert 流程数据

    契约：仅处理 `.json` 文件。
    失败语义：找不到超级用户时抛 `NoResultFound`。
    """
    settings_service = get_settings_service()
    flows_path = settings_service.settings.load_flows_path
    if not flows_path:
        return

    async with session_scope() as session:
        # 注意：按角色查找超级用户，避免用户名变更影响
        from langflow.services.database.models.user.model import User

        stmt = select(User).where(User.is_superuser == True)  # noqa: E712
        result = await session.exec(stmt)
        user = result.first()
        if user is None:
            msg = "No superuser found in the database"
            raise NoResultFound(msg)

        # 确保该用户的默认文件夹存在
        _ = await get_or_create_default_folder(session, user.id)

        for file_path in await asyncio.to_thread(Path(flows_path).iterdir):
            if not await anyio.Path(file_path).is_file() or file_path.suffix != ".json":
                continue
            await logger.ainfo(f"Loading flow from file: {file_path.name}")
            async with async_open(str(file_path), "r", encoding="utf-8") as f:
                content = await f.read()
            await upsert_flow_from_file(content, file_path.stem, session, user.id)


async def detect_github_url(url: str) -> str:
    """将 GitHub 仓库/分支/标签/提交 `URL` 解析为 `zip` 下载地址。"""
    if matched := re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)?/?$", url):
        owner, repo = matched.groups()

        repo = repo.removesuffix(".git")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
            response.raise_for_status()
            default_branch = response.json().get("default_branch")
            return f"https://github.com/{owner}/{repo}/archive/refs/heads/{default_branch}.zip"

    if matched := re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/tree/([\w\\/.-]+)", url):
        owner, repo, branch = matched.groups()
        if branch[-1] == "/":
            branch = branch[:-1]
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"

    if matched := re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/releases/tag/([\w\\/.-]+)", url):
        owner, repo, tag = matched.groups()
        if tag[-1] == "/":
            tag = tag[:-1]
        return f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"

    if matched := re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/commit/(\w+)/?$", url):
        owner, repo, commit = matched.groups()
        return f"https://github.com/{owner}/{repo}/archive/{commit}.zip"

    return url


async def load_bundles_from_urls() -> tuple[list[TemporaryDirectory], list[str]]:
    """从配置的 `bundle` 地址下载并导入流程/组件。

    关键路径：
    1) 解析 `URL`（支持 GitHub 多种形式）
    2) 下载 zip 并解析 `flows`/`components` 目录
    3) 导入流程并返回组件路径列表

    契约：无 bundle 配置则返回空列表。
    失败语义：下载或解析失败会抛异常由上层处理。
    """
    component_paths: set[str] = set()
    temp_dirs = []
    settings_service = get_settings_service()
    bundle_urls = settings_service.settings.bundle_urls
    if not bundle_urls:
        return [], []

    async with session_scope() as session:
        # 按角色查找超级用户，避免用户名变更影响
        from langflow.services.database.models.user.model import User

        stmt = select(User).where(User.is_superuser == True)  # noqa: E712
        result = await session.exec(stmt)
        user = result.first()
        if user is None:
            msg = "No superuser found in the database"
            raise NoResultFound(msg)
        user_id = user.id

        for url in bundle_urls:
            url_ = await detect_github_url(url)

            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url_)
                response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as zfile:
                dir_names = [f.filename for f in zfile.infolist() if f.is_dir() and "/" not in f.filename[:-1]]
                temp_dir = None
                for filename in zfile.namelist():
                    path = Path(filename)
                    for dir_name in dir_names:
                        if path.is_relative_to(f"{dir_name}flows/") and path.suffix == ".json":
                            file_content = zfile.read(filename)
                            await upsert_flow_from_file(file_content, path.stem, session, user_id)
                        elif path.is_relative_to(f"{dir_name}components/"):
                            if temp_dir is None:
                                temp_dir = await asyncio.to_thread(TemporaryDirectory)
                                temp_dirs.append(temp_dir)
                            component_paths.add(str(Path(temp_dir.name) / f"{dir_name}components"))
                            await asyncio.to_thread(zfile.extract, filename, temp_dir.name)

    return temp_dirs, list(component_paths)


async def upsert_flow_from_file(file_content: AnyStr, filename: str, session: AsyncSession, user_id: UUID) -> None:
    """从文件内容创建或更新流程。

    关键路径：
    1) 解析 `JSON` 并校验 `id`/`endpoint_name`
    2) 若存在则更新，否则创建并关联默认文件夹
    3) 写回数据库并更新时间戳

    失败语义：`id` 非法时记录错误并返回。
    """
    flow = orjson.loads(file_content)
    flow_endpoint_name = flow.get("endpoint_name")
    if _is_valid_uuid(filename):
        flow["id"] = filename
    flow_id = flow.get("id")

    if isinstance(flow_id, str):
        try:
            flow_id = UUID(flow_id)
        except ValueError:
            await logger.aerror(f"Invalid UUID string: {flow_id}")
            return

    existing = await find_existing_flow(session, flow_id, flow_endpoint_name)
    if existing:
        await logger.adebug(f"Found existing flow: {existing.name}")
        await logger.ainfo(f"Updating existing flow: {flow_id} with endpoint name {flow_endpoint_name}")
        for key, value in flow.items():
            if hasattr(existing, key):
                # 注意：`JSON` 字段与数据库模型并非完全一致，仅更新同名属性
                setattr(existing, key, value)
        existing.updated_at = datetime.now(tz=timezone.utc).astimezone()
        existing.user_id = user_id

        # 确保关联默认文件夹
        if existing.folder_id is None:
            folder = await get_or_create_default_folder(session, user_id)
            existing.folder_id = folder.id

        if isinstance(existing.id, str):
            try:
                existing.id = UUID(existing.id)
            except ValueError:
                await logger.aerror(f"Invalid UUID string: {existing.id}")
                return

        session.add(existing)
    else:
        await logger.ainfo(f"Creating new flow: {flow_id} with endpoint name {flow_endpoint_name}")

        # 新建流程绑定默认文件夹
        folder = await get_or_create_default_folder(session, user_id)
        flow["user_id"] = user_id
        flow["folder_id"] = folder.id
        flow = Flow.model_validate(flow)
        flow.updated_at = datetime.now(tz=timezone.utc).astimezone()

        session.add(flow)


async def find_existing_flow(session, flow_id, flow_endpoint_name):
    """按 `endpoint_name` 或 `id` 查找已存在的流程。"""
    if flow_endpoint_name:
        await logger.adebug(f"flow_endpoint_name: {flow_endpoint_name}")
        stmt = select(Flow).where(Flow.endpoint_name == flow_endpoint_name)
        if existing := (await session.exec(stmt)).first():
            await logger.adebug(f"Found existing flow by endpoint name: {existing.name}")
            return existing

    stmt = select(Flow).where(Flow.id == flow_id)
    if existing := (await session.exec(stmt)).first():
        await logger.adebug(f"Found existing flow by id: {flow_id}")
        return existing
    return None


async def create_or_update_starter_projects(all_types_dict: dict) -> None:
    """创建或更新 `starter projects`。

    关键路径：
    1) 根据配置决定是否创建/更新
    2) 加载示例项目并更新版本
    3) 写入数据库并记录日志

    契约：`all_types_dict` 为组件类型与模板的索引。
    """
    if not get_settings_service().settings.create_starter_projects:
        # 注意：关闭时直接跳过全部初始化逻辑
        return

    async with session_scope() as session:
        new_folder = await get_or_create_starter_folder(session)
        starter_projects = await load_starter_projects()

        if get_settings_service().settings.update_starter_projects:
            await logger.adebug("Updating starter projects")
        # 1) 清理已有 `starter projects`
            successfully_updated_projects = 0
            await delete_starter_projects(session, new_folder.id)
            # 注意：头像资源已从安装目录直接提供，无需复制到配置目录

            # 2) 使用最新组件版本更新 `starter projects`（会修改文件内容）
            for project_path, project in starter_projects:
                (
                    project_name,
                    project_description,
                    project_is_component,
                    updated_at_datetime,
                    project_data,
                    project_icon,
                    project_icon_bg_color,
                    project_gradient,
                    project_tags,
                ) = get_project_data(project)
                updated_project_data = update_projects_components_with_latest_component_versions(
                    project_data.copy(), all_types_dict
                )
                updated_project_data = update_edges_with_latest_component_versions(updated_project_data)
                if updated_project_data != project_data:
                    project_data = updated_project_data
                    await update_project_file(project_path, project, updated_project_data)

                try:
                    # 写入更新后的 `starter project`
                    create_new_project(
                        session=session,
                        project_name=project_name,
                        project_description=project_description,
                        project_is_component=project_is_component,
                        updated_at_datetime=updated_at_datetime,
                        project_data=project_data,
                        project_icon=project_icon,
                        project_icon_bg_color=project_icon_bg_color,
                        project_gradient=project_gradient,
                        project_tags=project_tags,
                        new_folder_id=new_folder.id,
                    )
                except Exception:  # noqa: BLE001
                    await logger.aexception(f"Error while creating starter project {project_name}")

                successfully_updated_projects += 1
            await logger.adebug(f"Successfully updated {successfully_updated_projects} starter projects")
        else:
            # 即使不更新，也需要补齐不存在的 `starter projects`
            await logger.adebug("Creating new starter projects")
            successfully_created_projects = 0
            existing_flows = await get_all_flows_similar_to_project(session, new_folder.id)
            existing_flow_names = [existing_flow.name for existing_flow in existing_flows]
            for _, project in starter_projects:
                (
                    project_name,
                    project_description,
                    project_is_component,
                    updated_at_datetime,
                    project_data,
                    project_icon,
                    project_icon_bg_color,
                    project_gradient,
                    project_tags,
                ) = get_project_data(project)
                if project_name not in existing_flow_names:
                    try:
                        create_new_project(
                            session=session,
                            project_name=project_name,
                            project_description=project_description,
                            project_is_component=project_is_component,
                            updated_at_datetime=updated_at_datetime,
                            project_data=project_data,
                            project_icon=project_icon,
                            project_icon_bg_color=project_icon_bg_color,
                            project_gradient=project_gradient,
                            project_tags=project_tags,
                            new_folder_id=new_folder.id,
                        )
                    except Exception:  # noqa: BLE001
                        await logger.aexception(f"Error while creating starter project {project_name}")
                    successfully_created_projects += 1
                await logger.adebug(f"Successfully created {successfully_created_projects} starter projects")


async def initialize_auto_login_default_superuser() -> None:
    """在 `AUTO_LOGIN` 模式下初始化默认超级用户。"""
    settings_service = get_settings_service()
    if not settings_service.auth_settings.AUTO_LOGIN:
        return
    # 注意：`AUTO_LOGIN` 使用默认凭据完成初始化，不保留明文密码
    from lfx.services.settings.constants import DEFAULT_SUPERUSER, DEFAULT_SUPERUSER_PASSWORD

    username = DEFAULT_SUPERUSER
    password = DEFAULT_SUPERUSER_PASSWORD.get_secret_value()
    if not username or not password:
        msg = "SUPERUSER and SUPERUSER_PASSWORD must be set in the settings if AUTO_LOGIN is true."
        raise ValueError(msg)

    async with session_scope() as async_session:
        super_user = await create_super_user(db=async_session, username=username, password=password)
        await get_variable_service().initialize_user_variables(super_user.id, async_session)
        # 若启用 `agentic` 体验，则初始化相关变量
        from langflow.api.utils.mcp.agentic_mcp import initialize_agentic_user_variables

        if get_settings_service().settings.agentic_experience:
            await initialize_agentic_user_variables(super_user.id, async_session)
        _ = await get_or_create_default_folder(async_session, super_user.id)
    await logger.adebug("Super user initialized")


async def get_or_create_default_folder(session: AsyncSession, user_id: UUID) -> FolderRead:
    """获取或创建用户默认文件夹。

    关键路径：
    1) 查询当前默认文件夹
    2) 如存在旧名称则执行迁移
    3) 并发冲突时回退重查

    契约：采用幂等写入策略以支持并发创建。
    失败语义：并发冲突且重查失败时抛 `ValueError`。
    """
    # 先检查当前默认文件夹
    stmt = select(Folder).where(Folder.user_id == user_id, Folder.name == DEFAULT_FOLDER_NAME)
    result = await session.exec(stmt)
    folder = result.first()
    if folder:
        return FolderRead.model_validate(folder, from_attributes=True)

    # 检查旧名称文件夹并在需要时迁移
    if DEFAULT_FOLDER_NAME not in LEGACY_FOLDER_NAMES:
        for legacy_name in LEGACY_FOLDER_NAMES:
            if legacy_name == DEFAULT_FOLDER_NAME:
                continue  # 若旧名称与默认一致则跳过

            legacy_stmt = select(Folder).where(Folder.user_id == user_id, Folder.name == legacy_name)
            legacy_result = await session.exec(legacy_stmt)
            legacy_folder = legacy_result.first()

            if legacy_folder:
                # 将旧文件夹改名为默认名称
                await logger.ainfo(
                    f"Migrating legacy folder '{legacy_name}' to '{DEFAULT_FOLDER_NAME}' for user {user_id}"
                )
                legacy_folder.name = DEFAULT_FOLDER_NAME
                legacy_folder.description = DEFAULT_FOLDER_DESCRIPTION
                session.add(legacy_folder)
                try:
                    await session.flush()
                    await session.refresh(legacy_folder)
                    return FolderRead.model_validate(legacy_folder, from_attributes=True)
                except sa.exc.IntegrityError:
                    # 若并发冲突则回滚并进入创建流程
                    await session.rollback()
                    break

    # 若不存在则创建新文件夹
    try:
        folder_obj = Folder(user_id=user_id, name=DEFAULT_FOLDER_NAME, description=DEFAULT_FOLDER_DESCRIPTION)
        session.add(folder_obj)
        await session.flush()
        await session.refresh(folder_obj)
    except sa.exc.IntegrityError as e:
        # 可能被其他 `worker` 并发创建
        await session.rollback()
        result = await session.exec(stmt)
        folder = result.first()
        if folder:
            return FolderRead.model_validate(folder, from_attributes=True)
        msg = "Failed to get or create default folder"
        raise ValueError(msg) from e
    return FolderRead.model_validate(folder_obj, from_attributes=True)


async def sync_flows_from_fs():
    """轮询文件系统并同步流程变更到数据库。

    关键路径：
    1) 周期性读取绑定 `fs_path` 的流程
    2) 比较文件 `mtime`，增量更新数据库字段
    3) 处理异常并在取消时退出

    契约：轮询间隔由 `fs_flows_polling_interval` 控制（毫秒转秒）。
    失败语义：数据库连接丢失时退出，其它异常记录并中断循环。
    """
    flow_mtimes = {}
    fs_flows_polling_interval = get_settings_service().settings.fs_flows_polling_interval / 1000
    storage_service = get_storage_service()
    try:
        while True:
            try:
                async with session_scope() as session:
                    stmt = select(Flow).where(col(Flow.fs_path).is_not(None))
                    flows = (await session.exec(stmt)).all()
                    for flow in flows:
                        mtime = flow_mtimes.setdefault(flow.id, 0)
                        # 路径解析：相对路径使用用户 `flows` 目录拼接
                        fs_path_str = flow.fs_path
                        if not Path(fs_path_str).is_absolute():
                            # 相对路径
                            path = storage_service.data_dir / "flows" / str(flow.user_id) / fs_path_str
                        else:
                            # 绝对路径直接使用
                            path = anyio.Path(fs_path_str)
                        try:
                            if await path.exists():
                                new_mtime = (await path.stat()).st_mtime
                                if new_mtime > mtime:
                                    update_data = orjson.loads(await path.read_text(encoding="utf-8"))
                                    try:
                                        for field_name in ("name", "description", "data", "locked"):
                                            if new_value := update_data.get(field_name):
                                                setattr(flow, field_name, new_value)
                                        if folder_id := update_data.get("folder_id"):
                                            flow.folder_id = UUID(folder_id)
                                        await session.flush()
                                        await session.refresh(flow)
                                    except Exception:  # noqa: BLE001
                                        await logger.aexception(
                                            f"Couldn't update flow {flow.id} in database from path {path}"
                                        )
                                    flow_mtimes[flow.id] = new_mtime
                        except Exception:  # noqa: BLE001
                            await logger.aexception(f"Error while handling flow file {path}")
            except asyncio.CancelledError:
                await logger.adebug("Flow sync cancelled")
                break
            except (sa.exc.OperationalError, ValueError) as e:
                if "no active connection" in str(e) or "connection is closed" in str(e):
                    await logger.adebug("Database connection lost, assuming shutdown")
                    break  # 数据库关闭时正常退出
                raise  # 其他数据库错误继续抛出
            except Exception:  # noqa: BLE001
                await logger.aexception("Error while syncing flows from database")
                break

            await asyncio.sleep(fs_flows_polling_interval)
    except asyncio.CancelledError:
        await logger.adebug("Flow sync task cancelled")
