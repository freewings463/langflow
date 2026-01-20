"""
模块名称：组件检索与元数据聚合

本模块用于从组件注册缓存中按条件检索组件元数据，主要服务于 `Agentic` 的组件发现与筛选。主要功能包括：
- 列表检索：按 `query`/`component_type`/`fields` 过滤组件元数据
- 单组件查询：按名称跨类型或指定类型查找
- 统计与枚举：输出可用类型及数量

关键组件：
- `list_all_components`：基于缓存返回可筛选的组件元数据
- `get_component_by_name`：按名称定位组件并裁剪字段
- `get_components_count`：提供数量统计

设计背景：组件定义集中于 `get_and_cache_all_types_dict`，用缓存换取接口响应速度。
注意事项：异常时返回空集合/None，调用方需区分“无结果”与“失败”语义。
"""

from typing import Any

from lfx.interface.components import get_and_cache_all_types_dict
from lfx.log.logger import logger
from lfx.services.settings.service import SettingsService


async def list_all_components(
    query: str | None = None,
    component_type: str | None = None,
    fields: list[str] | None = None,
    settings_service: SettingsService | None = None,
) -> list[dict[str, Any]]:
    """按条件返回组件元数据，供组件发现/筛选使用。

    契约：输入 `query`/`component_type`/`fields` 可为空；输出列表内字典至少含 `name`/`type`。
    副作用：读取组件缓存并写入日志 `Error listing components`/`Listing components completed`。
    失败语义：异常时返回空列表；字段不存在则静默忽略。
    关键路径（三步）：1) 拉取缓存字典 2) 类型与关键字过滤 3) 裁剪字段并组装结果
    异常流：缓存加载/过滤异常 -> 记录错误并返回 `[]`。
    性能瓶颈：组件总数×字段拷贝，`fields=None` 时返回体积最大。
    排障入口：日志关键字 `Error listing components`。
    决策：使用缓存索引而非实时扫描
    问题：实时扫描组件定义耗时且依赖外部服务
    方案：调用 `get_and_cache_all_types_dict` 并在内存中过滤
    代价：结果可能短时滞后于最新组件注册
    重评：当组件热更新频率显著升高或需强一致时
    """
    if settings_service is None:
        from langflow.services.deps import get_settings_service

        settings_service = get_settings_service()

    try:
        all_types_dict = await get_and_cache_all_types_dict(settings_service)
        results = []

        for comp_type, components in all_types_dict.items():
            if component_type and comp_type.lower() != component_type.lower():
                continue

            for component_name, component_data in components.items():
                if query:
                    name = component_name.lower()
                    display_name = component_data.get("display_name", "").lower()
                    description = component_data.get("description", "").lower()
                    query_lower = query.lower()

                    if query_lower not in name and query_lower not in display_name and query_lower not in description:
                        continue

                result = {
                    "name": component_name,
                    "type": comp_type,
                }

                if fields:
                    for field in fields:
                        if field == "name":
                            continue  # Already added
                        if field == "type":
                            continue  # Already added
                        if field in component_data:
                            result[field] = component_data[field]
                else:
                    result.update(component_data)

                results.append(result)

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error listing components: {e}")
        return []
    else:
        return results
    finally:
        await logger.ainfo("Listing components completed")


async def get_component_by_name(
    component_name: str,
    component_type: str | None = None,
    fields: list[str] | None = None,
    settings_service: SettingsService | None = None,
) -> dict[str, Any] | None:
    """按名称返回单个组件元数据，支持按类型收敛范围。

    契约：`component_name` 必填；`component_type` 为可选类型限定；返回 `dict` 或 `None`。
    副作用：读取组件缓存并记录日志 `Error getting component`/`Getting component completed`。
    失败语义：异常或未命中时返回 `None`，调用方需区分“未找到”与“失败”。
    关键路径（三步）：1) 读取缓存 2) 定位类型或跨类型匹配 3) 裁剪字段返回
    异常流：缓存加载异常 -> 记录错误并返回 `None`。
    性能瓶颈：跨类型搜索时为 O(类型×组件)。
    排障入口：日志关键字 `Error getting component`。
    决策：默认跨类型搜索而非强制指定类型
    问题：调用方往往只知道组件名
    方案：在未传 `component_type` 时遍历全部类型
    代价：跨类型搜索更慢
    重评：当类型数量显著增加或命名冲突频发时
    """
    if settings_service is None:
        from langflow.services.deps import get_settings_service

        settings_service = get_settings_service()

    try:
        all_types_dict = await get_and_cache_all_types_dict(settings_service)

        if component_type:
            components = all_types_dict.get(component_type, {})
            component_data = components.get(component_name)

            if component_data:
                result = {"name": component_name, "type": component_type}
                if fields:
                    for field in fields:
                        if field in {"name", "type"}:
                            continue
                        if field in component_data:
                            result[field] = component_data[field]
                else:
                    result.update(component_data)
                return result
        else:
            for comp_type, components in all_types_dict.items():
                if component_name in components:
                    component_data = components[component_name]
                    result = {"name": component_name, "type": comp_type}
                    if fields:
                        for field in fields:
                            if field in {"name", "type"}:
                                continue
                            if field in component_data:
                                result[field] = component_data[field]
                    else:
                        result.update(component_data)
                    return result

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting component {component_name}: {e}")
        return None
    else:
        return None
    finally:
        await logger.ainfo("Getting component completed")


async def get_all_component_types(settings_service: SettingsService | None = None) -> list[str]:
    """返回全部组件类型名的排序列表，用于 `UI`/检索的选项来源。

    契约：输出按字典序排序的类型列表。
    副作用：读取组件缓存并记录日志 `Error getting component types`。
    失败语义：异常时返回空列表。
    性能瓶颈：取决于缓存大小，但仅遍历键集合。
    排障入口：日志关键字 `Error getting component types`。
    决策：输出排序后的类型名而非原始顺序
    问题：调用方需要稳定可预测的枚举顺序
    方案：对类型键做字典序排序
    代价：每次调用增加一次排序开销
    重评：当调用频率很高且排序成为瓶颈时
    """
    if settings_service is None:
        from langflow.services.deps import get_settings_service

        settings_service = get_settings_service()

    try:
        all_types_dict = await get_and_cache_all_types_dict(settings_service)
        return sorted(all_types_dict.keys())

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error getting component types: {e}")
        return []
    finally:
        await logger.ainfo("Getting component types completed")


async def get_components_count(
    component_type: str | None = None, settings_service: SettingsService | None = None
) -> int:
    """统计组件数量，支持按类型收敛。

    契约：`component_type` 为空则统计全量；返回非负整数。
    副作用：读取缓存并记录日志 `Error counting components`。
    失败语义：异常时返回 `0`，调用方如需区分失败需结合日志。
    性能瓶颈：全量统计会遍历所有类型的组件集合。
    排障入口：日志关键字 `Error counting components`。
    决策：失败时返回 `0` 而非抛错
    问题：统计接口多用于 `UI` 概览，不希望中断主流程
    方案：捕获异常并返回默认值
    代价：`0` 可能掩盖真实故障
    重评：当调用方需要强一致或告警依赖该统计时
    """
    if settings_service is None:
        from langflow.services.deps import get_settings_service

        settings_service = get_settings_service()

    try:
        all_types_dict = await get_and_cache_all_types_dict(settings_service)

        if component_type:
            components = all_types_dict.get(component_type, {})
            return len(components)

        return sum(len(components) for components in all_types_dict.values())

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error counting components: {e}")
        return 0
    finally:
        await logger.ainfo("Counting components completed")


async def get_components_by_type(
    component_type: str,
    fields: list[str] | None = None,
    settings_service: SettingsService | None = None,
) -> list[dict[str, Any]]:
    """返回指定类型的组件列表，封装常见调用路径。

    契约：`component_type` 必填；返回列表内字典至少含 `name`/`type`。
    副作用：复用 `list_all_components` 的缓存读取与日志行为。
    失败语义：异常时返回空列表（由 `list_all_components` 兜底）。
    性能瓶颈：受组件数量与字段裁剪影响，`fields=None` 时返回体积最大。
    排障入口：日志关键字 `Error listing components`。
    决策：复用 `list_all_components` 而不重复实现过滤逻辑
    问题：避免两套过滤逻辑导致行为不一致
    方案：统一走同一入口并传入 `component_type`
    代价：引入一层函数跳转
    重评：当类型专用接口需要额外聚合信息时
    """
    return await list_all_components(component_type=component_type, fields=fields, settings_service=settings_service)
