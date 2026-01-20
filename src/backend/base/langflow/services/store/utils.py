"""
模块名称：`Store` 服务工具函数

本模块提供 `Store` 服务的辅助处理逻辑，主要用于标签处理、组件数据统计与版本查询。
主要功能包括：
- 处理上传标签结构转换。
- 依据用户点赞信息更新组件列表。
- 从 `PyPI` 获取最新 `langflow` 版本号。
- 统计组件节点类型分布。

关键组件：`process_tags_for_post`、`update_components_with_user_data`、`get_lf_version_from_pypi`。
设计背景：将通用处理逻辑从服务类中拆分以降低复杂度。
使用场景：组件上传、列表渲染与版本提示。
注意事项：外部依赖网络请求可能失败，调用方需容错。
"""

from typing import TYPE_CHECKING

import httpx
from lfx.log.logger import logger

if TYPE_CHECKING:
    from langflow.services.store.schema import ListComponentResponse
    from langflow.services.store.service import StoreService


def process_tags_for_post(component_dict):
    """将标签列表转换为 `Store` 所需结构。

    契约：输入组件字典，输出处理后的字典；副作用：原地修改 `component_dict`。
    关键路径：将字符串标签列表转为 `tags_id` 字典列表。
    决策：直接修改入参字典
    问题：上传接口要求标签结构化
    方案：将 `tags` 从字符串列表映射为对象列表
    代价：调用方需知晓入参被修改
    重评：若改为返回新字典以避免副作用
    """
    tags = component_dict.pop("tags", None)
    if tags and all(isinstance(tag, str) for tag in tags):
        component_dict["tags"] = [{"tags_id": tag} for tag in tags]
    return component_dict


async def update_components_with_user_data(
    components: list["ListComponentResponse"],
    store_service: "StoreService",
    store_api_key: str,
    *,
    liked: bool,
):
    """根据用户数据更新组件列表。

    契约：输入组件列表与 `store_api_key`，输出更新后的组件列表；副作用：可能访问远程接口。
    关键路径：获取用户点赞组件并设置 `liked_by_user`。
    异常流：远程请求失败向上抛出。
    决策：优先复用已有 `components` 以避免额外查询
    问题：需要补充 `liked_by_user` 状态
    方案：按需调用点赞查询接口
    代价：列表较大时需额外请求
    重评：若 `Store` 直接返回点赞状态
    """
    component_ids = [str(component.id) for component in components]
    if liked:
        # 注意：已按点赞过滤时，所有组件均为点赞。
        liked_by_user_ids = component_ids
    else:
        liked_by_user_ids = await store_service.get_liked_by_user_components(
            component_ids=component_ids,
            api_key=store_api_key,
        )
    # 注意：为每个组件写入 `liked_by_user`。
    for component in components:
        component.liked_by_user = str(component.id) in liked_by_user_ids

    return components


async def get_lf_version_from_pypi():
    """从 `PyPI` 获取最新 `langflow` 版本号。

    契约：无输入，输出版本号字符串或 `None`；副作用：发起网络请求。
    关键路径：请求 `PyPI` JSON 并读取 `info.version`。
    异常流：请求或解析失败时返回 `None`。
    排障入口：日志关键字 `Error getting the latest version`。
    决策：失败时返回 `None` 而非抛异常
    问题：版本检查不应阻断主流程
    方案：吞掉异常并记录 debug 日志
    代价：版本获取失败可能不易被注意
    重评：若需要强制版本提示
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://pypi.org/pypi/langflow/json")
        if response.status_code != httpx.codes.OK:
            return None
        return response.json()["info"]["version"]
    except Exception:  # noqa: BLE001
        logger.debug("Error getting the latest version of langflow from PyPI", exc_info=True)
        return None


def process_component_data(nodes_list):
    """统计组件节点类型分布。

    契约：输入节点列表，输出统计字典；副作用：无。
    关键路径：按 `node["id"]` 前缀分组并累加计数。
    决策：以 `id` 前缀作为节点类型标识
    问题：节点类型未提供显式字段
    方案：通过 `id` 前缀推断
    代价：`id` 格式变化会导致统计不准确
    重评：若节点提供明确类型字段
    """
    names = [node["id"].split("-")[0] for node in nodes_list]
    metadata = {}
    for name in names:
        if name in metadata:
            metadata[name]["count"] += 1
        else:
            metadata[name] = {"count": 1}
    metadata["total"] = len(names)

    return metadata
