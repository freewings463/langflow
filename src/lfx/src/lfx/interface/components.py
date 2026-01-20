"""
模块名称：组件索引与加载管理

本模块提供组件索引读取、动态扫描加载、缓存与遥测上报等能力，主要用于提升启动速度并支持开发模式热更新。主要功能包括：
- 读取预构建组件索引并进行完整性校验
- 在开发模式下按模块动态加载组件
- 管理组件缓存并按需加载元数据或完整模板
- 发送组件加载指标到遥测服务

关键组件：
- `ComponentCache`：组件缓存与加载状态跟踪
- `_read_component_index`：索引读取与校验
- `_load_components_dynamically`：动态扫描与加载
- `import_langflow_components`：统一加载入口

设计背景：组件数量较多，需在生产环境优先使用索引以降低启动耗时。
使用场景：服务启动、组件索引更新、开发调试与自定义组件加载。
注意事项：索引需通过 SHA256 校验；开发模式会绕过索引以反映实时代码变化。
"""

import asyncio
import hashlib
import importlib
import inspect
import json
import os
import pkgutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import orjson

from lfx.constants import BASE_COMPONENTS_PATH
from lfx.custom.utils import abuild_custom_components, create_component_template
from lfx.log.logger import logger
from lfx.utils.validate_cloud import (
    filter_disabled_components_from_dict,
    is_component_disabled_in_astra_cloud,
)

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService

MIN_MODULE_PARTS = 2
MIN_MODULE_PARTS_WITH_FILENAME = 4  # 注意：包含文件名的最小路径层级（`lfx.components.type.filename`）。
EXPECTED_RESULT_LENGTH = 2  # 注意：`_process_single_module` 预期返回二元组长度。


# 实现：使用对象封装缓存，避免散落的全局状态。
class ComponentCache:
    """组件缓存与加载状态。

    契约：缓存完整组件字典与已完全加载标记。
    副作用：持久化运行期缓存状态，供多处读取。
    失败语义：无（纯内存结构）。
    决策：使用单例对象而非模块级全局 dict。
    问题：全局 dict 可读性差且难以扩展状态字段。
    方案：封装为类并集中管理字段。
    代价：引入间接层，调用需经过对象访问。
    重评：当引入集中缓存服务或状态机时再调整。
    """

    def __init__(self):
        """初始化组件缓存。

        契约：`all_types_dict` 初始为 None，`fully_loaded_components` 为空。
        副作用：创建内存结构以承载缓存。
        失败语义：无。
        """
        self.all_types_dict: dict[str, Any] | None = None
        self.fully_loaded_components: dict[str, bool] = {}


# 注意：单例缓存实例。
component_cache = ComponentCache()


def _parse_dev_mode() -> tuple[bool, list[str] | None]:
    """解析 `LFX_DEV` 并返回开发模式配置。

    契约：返回 `(dev_mode_enabled, target_modules)`；`target_modules=None` 表示加载全部模块。
    副作用：无，仅读取环境变量。
    关键路径（三步）：1) 读取环境变量 2) 判断布尔/列表模式 3) 生成过滤列表。
    失败语义：解析失败时回退到关闭开发模式。
    决策：将 `0/false/no` 视为显式关闭。
    问题：同一环境变量既要支持开关也要支持列表筛选。
    方案：优先识别布尔语义，其次解析逗号列表。
    代价：语义分支增加，配置错误时可能回退到关闭。
    重评：当引入结构化配置文件后统一迁移配置入口。
    """
    lfx_dev = os.getenv("LFX_DEV", "").strip()
    if not lfx_dev:
        return (False, None)

    # 注意：布尔模式用于全量动态加载。
    if lfx_dev.lower() in {"1", "true", "yes"}:
        return (True, None)  # 全量动态加载

    # 注意：显式关闭避免误解析为列表模式。
    if lfx_dev.lower() in {"0", "false", "no"}:
        return (False, None)

    # 实现：列表模式允许只加载指定模块。
    modules = [m.strip().lower() for m in lfx_dev.split(",") if m.strip()]
    if modules:
        return (True, modules)

    return (False, None)


def _read_component_index(custom_path: str | None = None) -> dict | None:
    """读取并校验组件索引。

    契约：支持本地路径或 URL；校验通过返回索引 dict，否则返回 None。
    副作用：可能发起 HTTP 请求或读取本地文件。
    关键路径（三步）：1) 确定索引来源 2) 解析 JSON 3) 校验 SHA 与版本。
    失败语义：网络/解析/校验失败返回 None 并记录日志。
    决策：以 SHA256 与版本号双重校验索引。
    问题：索引文件可能被篡改或与版本不匹配。
    方案：使用内容哈希与版本一致性检查。
    代价：读取时多一次哈希计算与版本检查。
    重评：当索引引入签名机制或更严格校验时替换。
    """
    try:
        import lfx

        # 实现：优先使用自定义索引路径或 URL。
        if custom_path:
            # 注意：仅支持 http/https 远程索引。
            if custom_path.startswith(("http://", "https://")):
                # 实现：从远程拉取索引内容。
                import httpx

                try:
                    response = httpx.get(custom_path, timeout=10.0)
                    response.raise_for_status()
                    blob = orjson.loads(response.content)
                except httpx.HTTPError as e:
                    logger.warning(f"Failed to fetch component index from {custom_path}: {e}")
                    return None
                except orjson.JSONDecodeError as e:
                    logger.warning(f"Component index from {custom_path} is corrupted or invalid JSON: {e}")
                    return None
            else:
                # 实现：从本地文件路径加载索引。
                index_path = Path(custom_path)
                if not index_path.exists():
                    logger.warning(f"Custom component index not found at {custom_path}")
                    return None
                try:
                    blob = orjson.loads(index_path.read_bytes())
                except orjson.JSONDecodeError as e:
                    logger.warning(f"Component index at {custom_path} is corrupted or invalid JSON: {e}")
                    return None
        else:
            # 实现：默认使用内置索引文件。
            pkg_dir = Path(inspect.getfile(lfx)).parent
            index_path = pkg_dir / "_assets" / "component_index.json"

            if not index_path.exists():
                return None

            try:
                blob = orjson.loads(index_path.read_bytes())
            except orjson.JSONDecodeError as e:
                logger.warning(f"Built-in component index is corrupted or invalid JSON: {e}")
                return None

        # 安全：校验 SHA256，防止索引被篡改。
        tmp = dict(blob)
        sha = tmp.pop("sha256", None)
        if not sha:
            logger.warning("Component index missing SHA256 hash - index may be tampered")
            return None

        # 注意：使用 orjson 与构建脚本保持一致的排序与序列化规则。
        calc = hashlib.sha256(orjson.dumps(tmp, option=orjson.OPT_SORT_KEYS)).hexdigest()
        if sha != calc:
            logger.warning(
                "Component index integrity check failed - SHA256 mismatch (file may be corrupted or tampered)"
            )
            return None

        # 兼容性：索引版本需与当前 langflow 版本一致。
        from importlib.metadata import version

        installed_version = version("langflow")
        if blob.get("version") != installed_version:
            logger.debug(
                f"Component index version mismatch: index={blob.get('version')}, installed={installed_version}"
            )
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Unexpected error reading component index: {type(e).__name__}: {e}")
        return None
    return blob


def _get_cache_path() -> Path:
    """返回组件索引缓存文件路径。

    契约：确保缓存目录存在并返回固定文件名路径。
    副作用：可能创建缓存目录。
    失败语义：目录创建失败将抛出异常。
    决策：缓存存放于用户级缓存目录。
    问题：索引需要跨进程复用且不污染工作目录。
    方案：使用 `platformdirs.user_cache_dir`。
    代价：不同系统路径不同，排障时需定位目录。
    重评：当引入集中缓存服务时改为远端或数据库存储。
    """
    from platformdirs import user_cache_dir

    cache_dir = Path(user_cache_dir("lfx", "langflow"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "component_index.json"


def _save_generated_index(modules_dict: dict) -> None:
    """将动态生成的组件索引写入缓存。

    契约：输入为按类别组织的组件字典；写入缓存文件。
    副作用：写文件到用户缓存目录。
    关键路径（三步）：1) 生成索引结构 2) 计算 SHA256 3) 持久化写入。
    失败语义：写入失败仅记录日志，不影响主流程。
    决策：缓存索引用 JSON + SHA256。
    问题：动态加载成本高，需要下次快速启动。
    方案：将索引落盘并带完整性校验。
    代价：首次动态加载时增加写盘成本。
    重评：当索引生成成本降低或有集中缓存后移除。
    """
    try:
        cache_path = _get_cache_path()

        # 实现：将模块字典转换为 entries 结构。
        entries = [[top_level, components] for top_level, components in modules_dict.items()]

        # 实现：计算模块与组件数量统计。
        num_modules = len(modules_dict)
        num_components = sum(len(components) for components in modules_dict.values())

        # 实现：读取 langflow 版本号。
        from importlib.metadata import version

        langflow_version = version("langflow")

        # 实现：构建索引结构体。
        index = {
            "version": langflow_version,
            "metadata": {
                "num_modules": num_modules,
                "num_components": num_components,
            },
            "entries": entries,
        }

        # 实现：计算并写入 SHA256。
        payload = orjson.dumps(index, option=orjson.OPT_SORT_KEYS)
        index["sha256"] = hashlib.sha256(payload).hexdigest()

        # 实现：写入缓存文件。
        json_bytes = orjson.dumps(index, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        cache_path.write_bytes(json_bytes)

        logger.debug(f"Saved generated component index to cache: {cache_path}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Failed to save generated index to cache: {e}")


async def _send_telemetry(
    telemetry_service: Any,
    index_source: str,
    modules_dict: dict,
    dev_mode: bool,  # noqa: FBT001
    target_modules: list[str] | None,
    start_time_ms: int,
) -> None:
    """发送组件索引加载的遥测数据。

    契约：当 `telemetry_service` 存在时上报加载指标。
    副作用：异步网络/日志操作。
    关键路径（三步）：1) 计算指标 2) 构造 payload 3) 调用遥测服务。
    失败语义：遥测失败被吞掉并记录 debug，不影响组件加载。
    决策：遥测失败不阻塞组件加载。
    问题：遥测不可用会导致启动失败风险。
    方案：捕获异常并记录日志。
    代价：可能丢失部分监控数据。
    重评：当遥测成为强依赖或需保证一致性时调整。
    """
    if not telemetry_service:
        return

    try:
        # 实现：计算加载统计指标。
        num_modules = len(modules_dict)
        num_components = sum(len(components) for components in modules_dict.values())
        load_time_ms = int(time.time() * 1000) - start_time_ms
        filtered_modules = ",".join(target_modules) if target_modules else None

        # 注意：动态导入 payload 避免循环依赖。
        from langflow.services.telemetry.schema import ComponentIndexPayload

        payload = ComponentIndexPayload(
            index_source=index_source,
            num_modules=num_modules,
            num_components=num_components,
            dev_mode=dev_mode,
            filtered_modules=filtered_modules,
            load_time_ms=load_time_ms,
        )

        await telemetry_service.log_component_index(payload)
    except Exception as e:  # noqa: BLE001
        # 注意：遥测失败不影响组件加载。
        await logger.adebug(f"Failed to send component index telemetry: {e}")


async def _load_from_index_or_cache(
    settings_service: Optional["SettingsService"] = None,
) -> tuple[dict[str, Any], str | None]:
    """从预构建索引或缓存加载组件字典。

    契约：返回 `(modules_dict, index_source)`；`index_source` 为 `builtin`/`cache`/`None`。
    副作用：可能读取文件或远程索引。
    关键路径（三步）：1) 读取内置/自定义索引 2) 失败则尝试缓存 3) 过滤禁用组件。
    失败语义：无法加载时返回空字典与 None。
    决策：优先使用内置索引，其次缓存。
    问题：启动时需快速加载且允许离线缓存兜底。
    方案：内置索引失败后回退缓存索引。
    代价：缓存可能过期导致结果不一致。
    重评：当引入版本化缓存或签名校验时调整顺序。
    """
    modules_dict: dict[str, Any] = {}

    # 实现：优先尝试内置/自定义索引。
    custom_index_path = None
    if settings_service and settings_service.settings.components_index_path:
        custom_index_path = settings_service.settings.components_index_path
        await logger.adebug(f"Using custom component index: {custom_index_path}")

    index = _read_component_index(custom_index_path)
    if index and "entries" in index:
        source = custom_index_path or "built-in index"
        await logger.adebug(f"Loading components from {source}")
        # 实现：从索引 entries 重建模块字典。
        for top_level, components in index["entries"]:
            if top_level not in modules_dict:
                modules_dict[top_level] = {}
            modules_dict[top_level].update(components)
        # 注意：过滤 Astra Cloud 禁用组件。
        modules_dict = filter_disabled_components_from_dict(modules_dict)
        await logger.adebug(f"Loaded {len(modules_dict)} component categories from index")
        return modules_dict, "builtin"

    # 实现：索引不可用时回退到缓存。
    await logger.adebug("Prebuilt index not available, checking cache")
    try:
        cache_path = _get_cache_path()
    except Exception as e:  # noqa: BLE001
        await logger.adebug(f"Cache load failed: {e}")
    else:
        if cache_path.exists():
            await logger.adebug(f"Attempting to load from cache: {cache_path}")
            index = _read_component_index(str(cache_path))
            if index and "entries" in index:
                await logger.adebug("Loading components from cached index")
                for top_level, components in index["entries"]:
                    if top_level not in modules_dict:
                        modules_dict[top_level] = {}
                    modules_dict[top_level].update(components)
                # 注意：过滤 Astra Cloud 禁用组件。
                modules_dict = filter_disabled_components_from_dict(modules_dict)
                await logger.adebug(f"Loaded {len(modules_dict)} component categories from cache")
                return modules_dict, "cache"

    return modules_dict, None


async def _load_components_dynamically(
    target_modules: list[str] | None = None,
) -> dict[str, Any]:
    """动态扫描并加载组件模块。

    契约：可选 `target_modules` 仅加载指定模块；返回按顶层分类的组件字典。
    副作用：动态导入模块并实例化组件。
    关键路径（三步）：1) 枚举模块名 2) 并行处理模块 3) 合并结果。
    失败语义：单个模块失败会记录日志并跳过。
    决策：并行处理模块以降低总耗时。
    问题：组件模块数量多，串行加载耗时过长。
    方案：使用 `asyncio.to_thread` 并发导入解析。
    代价：并发导入可能增加瞬时资源占用。
    重评：当引入更强的并发控制或进程池时调整。
    """
    modules_dict: dict[str, Any] = {}

    try:
        import lfx.components as components_pkg
    except ImportError as e:
        await logger.aerror(f"Failed to import langflow.components package: {e}", exc_info=True)
        return modules_dict

    # 实现：收集需要处理的模块名列表。
    module_names = []
    for _, modname, _ in pkgutil.walk_packages(components_pkg.__path__, prefix=components_pkg.__name__ + "."):
        # 注意：跳过 deactivated 目录。
        if "deactivated" in modname:
            continue

        # 实现：解析模块名便于后续判断。
        parts = modname.split(".")
        if len(parts) > MIN_MODULE_PARTS:
            component_type = parts[2]

            # 安全：过滤 Astra Cloud 禁用组件。
            if len(parts) >= MIN_MODULE_PARTS_WITH_FILENAME:
                module_filename = parts[3]
                if is_component_disabled_in_astra_cloud(component_type.lower(), module_filename):
                    continue

            # 实现：若指定 target_modules，则按顶层模块过滤。
            if target_modules and component_type.lower() not in target_modules:
                continue

        module_names.append(modname)

    if target_modules:
        await logger.adebug(f"Found {len(module_names)} modules matching filter")

    if not module_names:
        return modules_dict

    # 实现：并行处理模块以加速加载。
    tasks = [asyncio.to_thread(_process_single_module, modname) for modname in module_names]

    # 实现：等待所有模块处理完成。
    try:
        module_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error during parallel module processing: {e}", exc_info=True)
        return modules_dict

    # 实现：合并各模块返回的组件字典。
    for result in module_results:
        if isinstance(result, Exception):
            await logger.awarning(f"Module processing failed: {result}")
            continue

        if result and isinstance(result, tuple) and len(result) == EXPECTED_RESULT_LENGTH:
            top_level, components = result
            if top_level and components:
                if top_level not in modules_dict:
                    modules_dict[top_level] = {}
                modules_dict[top_level].update(components)

    return modules_dict


async def _load_full_dev_mode() -> tuple[dict[str, Any], str]:
    """开发模式下全量动态加载组件。

    契约：返回 `(modules_dict, "dynamic")`。
    副作用：动态导入并实例化全部组件。
    失败语义：加载失败时返回空字典。
    决策：开发模式直接绕过索引。
    问题：开发时需要实时反映代码变更。
    方案：全量动态扫描并加载。
    代价：启动更慢且资源占用更高。
    重评：当支持热更新索引时减少全量加载。
    """
    await logger.adebug("LFX_DEV full mode: loading all modules dynamically")
    modules_dict = await _load_components_dynamically(target_modules=None)
    return modules_dict, "dynamic"


async def _load_selective_dev_mode(
    settings_service: Optional["SettingsService"],
    target_modules: list[str],
) -> tuple[dict[str, Any], str]:
    """开发模式下选择性动态重载模块。

    契约：从索引加载基础组件，再用动态加载替换目标模块。
    副作用：动态导入目标模块并覆盖缓存内容。
    关键路径（三步）：1) 读取索引 2) 动态加载目标模块 3) 合并覆盖。
    失败语义：目标模块加载失败则保留索引版本。
    决策：保留未改动模块以降低加载成本。
    问题：全量动态加载成本高，开发只改动部分模块。
    方案：索引 + 目标模块动态重载。
    代价：索引与动态模块之间可能出现版本差异。
    重评：当提供模块级热更新时替代该策略。
    """
    await logger.adebug(f"LFX_DEV selective mode: reloading {target_modules}")
    modules_dict, _ = await _load_from_index_or_cache(settings_service)

    # 实现：动态重载指定模块。
    dynamic_modules = await _load_components_dynamically(target_modules=target_modules)

    # 实现：合并并覆盖目标模块组件。
    for top_level, components in dynamic_modules.items():
        if top_level not in modules_dict:
            modules_dict[top_level] = {}
        modules_dict[top_level].update(components)

    await logger.adebug(f"Reloaded {len(target_modules)} module(s), kept others from index")
    return modules_dict, "dynamic"


async def _load_production_mode(
    settings_service: Optional["SettingsService"],
) -> tuple[dict[str, Any], str]:
    """生产模式组件加载（索引 -> 缓存 -> 动态）。

    契约：按顺序尝试索引、缓存与动态加载；返回 `(modules_dict, index_source)`。
    副作用：可能写入缓存索引。
    关键路径（三步）：1) 读取索引/缓存 2) 失败则动态加载 3) 动态结果落盘缓存。
    失败语义：动态加载失败时返回空字典。
    决策：生产优先索引，动态作为兜底。
    问题：需要兼顾启动速度与可用性。
    方案：索引优先，失败时动态加载并缓存。
    代价：首次动态加载耗时更高。
    重评：当索引生成可靠性提高或支持增量更新时优化链路。
    """
    modules_dict, index_source = await _load_from_index_or_cache(settings_service)

    if not index_source:
        # 注意：无索引或缓存时回退动态加载。
        await logger.adebug("Falling back to dynamic loading")
        modules_dict = await _load_components_dynamically(target_modules=None)
        index_source = "dynamic"

        # 注意：将动态结果写入缓存以加速下次启动。
        if modules_dict:
            await logger.adebug("Saving generated component index to cache")
            _save_generated_index(modules_dict)

    return modules_dict, index_source


async def import_langflow_components(
    settings_service: Optional["SettingsService"] = None,
    telemetry_service: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """异步加载内置组件并返回组件字典。

    契约：返回包含 `components` 键的字典，值为组件模板集合。
    副作用：可能读写索引缓存并发送遥测。
    关键路径（三步）：1) 解析开发模式 2) 选择加载策略 3) 发送遥测并返回结果。
    失败语义：加载失败时返回空组件字典。
    决策：以开发模式参数驱动加载策略。
    问题：需要在速度、正确性与开发体验之间权衡。
    方案：生产用索引链路，开发按需动态加载。
    代价：策略分支增加维护成本。
    重评：当统一加载链路且性能可接受时简化策略。
    """
    start_time_ms: int = int(time.time() * 1000)
    dev_mode_enabled, target_modules = _parse_dev_mode()

    # 实现：根据开发模式选择加载策略。
    if dev_mode_enabled and not target_modules:
        modules_dict, index_source = await _load_full_dev_mode()
    elif dev_mode_enabled and target_modules:
        modules_dict, index_source = await _load_selective_dev_mode(settings_service, target_modules)
    else:
        modules_dict, index_source = await _load_production_mode(settings_service)

    # 实现：发送加载遥测。
    await _send_telemetry(
        telemetry_service, index_source, modules_dict, dev_mode_enabled, target_modules, start_time_ms
    )

    return {"components": modules_dict}


def _process_single_module(modname: str) -> tuple[str, dict] | None:
    """处理单个模块并提取组件模板。

    契约：返回 `(top_level, components_dict)` 或 None。
    副作用：动态导入模块并实例化组件。
    关键路径（三步）：1) 导入模块 2) 过滤组件类 3) 构建模板并汇总。
    失败语义：导入失败返回 None；单个类失败会跳过并记录日志。
    决策：仅处理当前模块内定义的类。
    问题：跨模块继承类会被重复处理或引入错误。
    方案：校验 `__module__` 与 `modname` 一致。
    代价：可能遗漏动态生成的跨模块类。
    重评：当需要更复杂的组件发现机制时调整。
    """
    try:
        module = importlib.import_module(modname)
    except Exception as e:  # noqa: BLE001
        # 注意：导入异常不应阻断启动，暂以日志方式记录。
        # TODO：后续可将错误友好地展示到 UI。
        logger.error(f"Failed to import module {modname}: {e}", exc_info=True)
        return None
    # 实现：提取 `lfx.components.<type>` 中的顶层类别名。
    mod_parts = modname.split(".")
    if len(mod_parts) <= MIN_MODULE_PARTS:
        return None

    top_level = mod_parts[2]
    module_components = {}

    # 性能：缓存 `getattr` 以减少循环内开销。
    _getattr = getattr

    # 实现：仅遍历当前模块内定义的类。
    failed_count = []
    for name, obj in vars(module).items():
        if not isinstance(obj, type):
            continue

        # 注意：跳过外部模块导入的类，避免重复与误识别。
        if obj.__module__ != modname:
            continue

        # 实现：仅识别具备组件基类标识的类。
        if not (
            _getattr(obj, "code_class_base_inheritance", None) is not None
            or _getattr(obj, "_code_class_base_inheritance", None) is not None
        ):
            continue

        try:
            comp_instance = obj()
            # 注意：`modname` 为模块全名，需追加类名形成组件路径。
            full_module_name = f"{modname}.{name}"
            comp_template, _ = create_component_template(
                component_extractor=comp_instance, module_name=full_module_name
            )
            component_name = obj.name if hasattr(obj, "name") and obj.name else name
            module_components[component_name] = comp_template
        except Exception as e:  # noqa: BLE001
            failed_count.append(f"{name}: {e}")
            continue

    if failed_count:
        logger.warning(
            f"Skipped {len(failed_count)} component class{'es' if len(failed_count) != 1 else ''} "
            f"in module '{modname}' due to instantiation failure: {', '.join(failed_count)}"
        )
    logger.debug(f"Processed module {modname}")
    return (top_level, module_components)


async def _determine_loading_strategy(settings_service: "SettingsService") -> dict[str, Any]:
    """根据配置决定并执行组件加载策略。

    契约：返回自定义组件字典或空字典。
    副作用：可能触发自定义组件加载与日志记录。
    关键路径（三步）：1) 初始化缓存 2) 判断懒加载/路径 3) 返回结果。
    失败语义：加载异常由底层函数处理并返回空结果。
    决策：优先使用懒加载以减少启动成本。
    问题：自定义组件可能数量多且加载成本高。
    方案：在启用懒加载时仅构建元数据。
    代价：首次访问组件可能需要二次加载。
    重评：当组件加载成本降低或缓存足够时取消懒加载。
    """
    component_cache.all_types_dict = {}
    if settings_service.settings.lazy_load_components:
        # 实现：懒加载模式只加载元数据。
        await logger.adebug("Using partial component loading")
        component_cache.all_types_dict = await aget_component_metadata(settings_service.settings.components_path)
    elif settings_service.settings.components_path:
        # 实现：全量加载时只加载自定义组件路径。
        custom_paths = [p for p in settings_service.settings.components_path if p != BASE_COMPONENTS_PATH]
        if custom_paths:
            component_cache.all_types_dict = await aget_all_types_dict(custom_paths)

    # 观测：记录自定义组件加载数量。
    components_dict = component_cache.all_types_dict or {}
    component_count = sum(len(comps) for comps in components_dict.get("components", {}).values())
    if component_count > 0 and settings_service.settings.components_path:
        await logger.adebug(
            f"Built {component_count} custom components from {settings_service.settings.components_path}"
        )

    return component_cache.all_types_dict or {}


async def get_and_cache_all_types_dict(
    settings_service: "SettingsService",
    telemetry_service: Any | None = None,
):
    """获取并缓存完整组件类型字典。

    契约：返回合并后的组件字典（内置 + 自定义）。
    副作用：可能触发组件加载与遥测上报，并写入内存缓存。
    关键路径（三步）：1) 加载内置组件 2) 载入自定义组件 3) 合并并缓存。
    失败语义：加载失败时返回空字典或部分结果。
    决策：缓存层统一保存“扁平化”后的组件字典。
    问题：调用方需要直接可用的组件映射而非嵌套结构。
    方案：合并内置与自定义后存入 `component_cache`。
    代价：缓存体积增大，占用内存。
    重评：当引入分层缓存或按需加载时调整合并策略。
    """
    if component_cache.all_types_dict is None:
        await logger.adebug("Building components cache")

        langflow_components = await import_langflow_components(settings_service, telemetry_service)
        custom_components_dict = await _determine_loading_strategy(settings_service)

        # 实现：将自定义组件字典拍平以便统一合并。
        custom_flat = custom_components_dict.get("components", custom_components_dict) or {}

        # 实现：合并内置与自定义组件。
        component_cache.all_types_dict = {
            **langflow_components["components"],
            **custom_flat,
        }
        component_count = sum(len(comps) for comps in component_cache.all_types_dict.values())
        await logger.adebug(f"Loaded {component_count} components")
    return component_cache.all_types_dict


async def aget_all_types_dict(components_paths: list[str]):
    """全量加载组件并返回类型字典。

    契约：`components_paths` 为组件路径列表；返回完整组件模板字典。
    副作用：加载并实例化自定义组件。
    失败语义：异常由下层加载函数处理。
    决策：交由 `abuild_custom_components` 统一构建。
    问题：避免重复实现自定义组件加载逻辑。
    方案：复用现有构建函数。
    代价：加载粒度较粗。
    重评：当需要更细粒度加载时拆分入口。
    """
    return await abuild_custom_components(components_paths=components_paths)


async def aget_component_metadata(components_paths: list[str]):
    """异步获取组件最小元数据。

    契约：返回仅包含基本描述与模板骨架的字典，并标记 `lazy_loaded=True`。
    副作用：扫描文件系统并构建元数据。
    关键路径（三步）：1) 发现组件类型 2) 发现组件名称 3) 生成元数据骨架。
    失败语义：路径不存在则跳过并返回空字典。
    决策：懒加载模式仅生成最小元数据。
    问题：全量加载会显著增加启动时间。
    方案：只扫描文件与构建骨架。
    代价：首次访问组件需要二次加载。
    重评：当缓存足够或启动成本降低时切换为全量加载。
    """
    # 实现：构建仅含基础信息的组件字典骨架。

    components_dict: dict = {"components": {}}

    if not components_paths:
        return components_dict

    # 实现：发现组件类型目录。
    component_types = await discover_component_types(components_paths)
    await logger.adebug(f"Discovered {len(component_types)} component types: {', '.join(component_types)}")

    # 实现：遍历每个组件类型目录。
    for component_type in component_types:
        components_dict["components"][component_type] = {}

        # 实现：发现该类型下的组件名称。
        component_names = await discover_component_names(component_type, components_paths)
        await logger.adebug(f"Found {len(component_names)} components for type {component_type}")

        # 实现：创建仅含基础元数据的条目。
        for name in component_names:
            # 实现：生成单组件元数据。
            metadata = await get_component_minimal_metadata(component_type, name, components_paths)

            if metadata:
                components_dict["components"][component_type][name] = metadata
                # 注意：标记为懒加载，后续需完整加载。
                components_dict["components"][component_type][name]["lazy_loaded"] = True

    return components_dict


async def discover_component_types(components_paths: list[str]) -> list[str]:
    """扫描路径以发现组件类型。

    契约：返回已发现的类型列表（含标准类型补充）。
    副作用：读取文件系统目录。
    失败语义：路径不存在则跳过。
    决策：补充标准类型以覆盖未落盘目录。
    问题：部分类型可能在路径中不存在但仍需暴露给 UI。
    方案：追加固定类型集合。
    代价：可能出现无实际组件的类型。
    重评：当类型可通过索引准确获取时移除补充。
    """
    component_types: set[str] = set()

    for path in components_paths:
        path_obj = Path(path)
        if not path_obj.exists():
            continue

        for item in path_obj.iterdir():
            # 注意：仅包含非 `_`/`.` 开头的目录。
            if item.is_dir() and not item.name.startswith(("_", ".")):
                component_types.add(item.name)

    # 实现：追加系统内置的标准类型集合。
    standard_types = {
        "agents",
        "chains",
        "embeddings",
        "llms",
        "memories",
        "prompts",
        "tools",
        "retrievers",
        "textsplitters",
        "toolkits",
        "utilities",
        "vectorstores",
        "custom_components",
        "documentloaders",
        "outputparsers",
        "wrappers",
    }

    component_types.update(standard_types)

    return sorted(component_types)


async def discover_component_names(component_type: str, components_paths: list[str]) -> list[str]:
    """扫描指定类型目录获取组件名称列表。

    契约：返回去重后的组件名列表。
    副作用：遍历文件系统目录。
    失败语义：目录不存在则返回空列表。
    决策：仅识别 `.py` 文件且排除 `__` 前缀。
    问题：仅将单文件组件视为可加载对象。
    方案：过滤掉非 Python 文件与私有模块。
    代价：忽略包级组件（目录形式）。
    重评：当组件以包形式组织时扩展识别规则。
    """
    component_names: set[str] = set()

    for path in components_paths:
        type_dir = Path(path) / component_type

        if type_dir.exists():
            for filename in type_dir.iterdir():
                # 注意：排除 `__init__.py` 等私有模块。
                if filename.name.endswith(".py") and not filename.name.startswith("__"):
                    component_name = filename.name[:-3]  # 注意：去掉 .py 扩展名。
                    component_names.add(component_name)

    return sorted(component_names)


async def get_component_minimal_metadata(component_type: str, component_name: str, components_paths: list[str]):
    """获取单组件最小元数据（不加载实现）。

    契约：返回包含模板骨架的元数据字典；找不到文件返回 None。
    副作用：检查文件存在性。
    失败语义：组件文件不存在时返回 None。
    决策：使用默认描述与模板骨架满足 UI 展示。
    问题：懒加载阶段无法执行组件代码获取真实元数据。
    方案：构造占位元数据并标记 lazy_loaded。
    代价：描述可能不准确，需要后续加载修正。
    重评：当可从索引读取真实元数据时替换。
    """
    # 实现：构造 UI 需要的基础元数据结构。
    metadata = {
        "display_name": component_name.replace("_", " ").title(),
        "name": component_name,
        "type": component_type,
        "description": f"A {component_type} component (not fully loaded)",
        "template": {
            "_type": component_type,
            "inputs": {},
            "outputs": {},
            "output_types": [],
            "documentation": f"A {component_type} component",
            "display_name": component_name.replace("_", " ").title(),
            "base_classes": [component_type],
        },
    }

    # 实现：验证组件文件是否存在。
    component_path = None
    for path in components_paths:
        candidate_path = Path(path) / component_type / f"{component_name}.py"
        if candidate_path.exists():
            component_path = candidate_path
            break

    if not component_path:
        return None

    return metadata


async def ensure_component_loaded(component_type: str, component_name: str, settings_service: "SettingsService"):
    """确保组件已从懒加载状态升级为全量加载。

    契约：若组件标记为 `lazy_loaded` 则加载完整模板并替换。
    副作用：加载组件实现并修改缓存字典。
    失败语义：加载失败记录日志并保持原元数据。
    决策：按组件粒度补全加载而非全量加载。
    问题：懒加载提升启动速度，但单组件使用时需完整信息。
    方案：按需加载单组件并更新缓存。
    代价：首次访问某组件会有额外延迟。
    重评：当用户批量访问同类组件时评估预加载。
    """
    # 实现：已完全加载则直接返回。
    component_key = f"{component_type}:{component_name}"
    if component_key in component_cache.fully_loaded_components:
        return

    # 实现：缓存不存在或组件未发现则跳过。
    if (
        not component_cache.all_types_dict
        or "components" not in component_cache.all_types_dict
        or component_type not in component_cache.all_types_dict["components"]
        or component_name not in component_cache.all_types_dict["components"][component_type]
    ):
        return

    # 实现：仅对懒加载组件执行补全。
    if component_cache.all_types_dict["components"][component_type][component_name].get("lazy_loaded", False):
        await logger.adebug(f"Fully loading component {component_type}:{component_name}")

        # 实现：仅加载目标组件。
        full_component = await load_single_component(
            component_type, component_name, settings_service.settings.components_path
        )

        if full_component:
            # 实现：用完整模板替换占位元数据。
            component_cache.all_types_dict["components"][component_type][component_name] = full_component
            # 实现：移除 lazy_loaded 标记。
            if "lazy_loaded" in component_cache.all_types_dict["components"][component_type][component_name]:
                del component_cache.all_types_dict["components"][component_type][component_name]["lazy_loaded"]

            # 实现：记录已完全加载状态。
            component_cache.fully_loaded_components[component_key] = True
            await logger.adebug(f"Component {component_type}:{component_name} fully loaded")
        else:
            await logger.awarning(f"Failed to fully load component {component_type}:{component_name}")


async def load_single_component(component_type: str, component_name: str, components_paths: list[str]):
    """加载单个组件的完整模板。

    契约：返回完整组件模板或 None。
    副作用：动态导入组件实现。
    失败语义：各种导入或结构错误会被捕获并记录日志。
    决策：集中捕获常见异常并降级为 None。
    问题：单组件加载失败不应中断整体流程。
    方案：细分异常类型并记录错误。
    代价：可能隐藏真实错误栈。
    重评：当需要强一致性或调试时考虑抛出异常。
    """
    from lfx.custom.utils import get_single_component_dict

    try:
        # 实现：委托给单组件加载工具函数。
        return await get_single_component_dict(component_type, component_name, components_paths)
    except (ImportError, ModuleNotFoundError) as e:
        # 排障：组件或依赖导入失败。
        await logger.aerror(f"Import error loading component {component_type}:{component_name}: {e!s}")
        return None
    except (AttributeError, TypeError) as e:
        # 排障：组件结构或类型不符合预期。
        await logger.aerror(f"Component structure error for {component_type}:{component_name}: {e!s}")
        return None
    except FileNotFoundError as e:
        # 排障：文件不存在。
        await logger.aerror(f"File not found for component {component_type}:{component_name}: {e!s}")
        return None
    except ValueError as e:
        # 排障：配置值非法。
        await logger.aerror(f"Invalid configuration for component {component_type}:{component_name}: {e!s}")
        return None
    except (KeyError, IndexError) as e:
        # 排障：数据结构访问错误。
        await logger.aerror(f"Data structure error for component {component_type}:{component_name}: {e!s}")
        return None
    except RuntimeError as e:
        # 排障：运行时错误。
        await logger.aerror(f"Runtime error loading component {component_type}:{component_name}: {e!s}")
        await logger.adebug("Full traceback for runtime error", exc_info=True)
        return None
    except OSError as e:
        # 排障：系统级错误（权限/文件系统等）。
        await logger.aerror(f"OS error loading component {component_type}:{component_name}: {e!s}")
        return None


# 注意：提供按类型加载的便捷函数。
async def get_type_dict(component_type: str, settings_service: Optional["SettingsService"] = None):
    """获取指定组件类型的字典，必要时触发加载。

    契约：返回该类型组件字典或空字典。
    副作用：可能触发全量或按需加载。
    关键路径（三步）：1) 获取 settings_service 2) 确保缓存存在 3) 确保组件完全加载。
    失败语义：找不到类型时返回空字典。
    决策：懒加载模式下逐个补全组件。
    问题：保证按类型访问时获得完整模板。
    方案：遍历并调用 `ensure_component_loaded`。
    代价：首次访问该类型可能较慢。
    重评：当需要批量预加载时改为类型级并行加载。
    """
    if settings_service is None:
        # 注意：延迟导入以避免循环依赖。
        from langflow.services.deps import get_settings_service

        settings_service = get_settings_service()

    # 实现：确保缓存已构建。
    if component_cache.all_types_dict is None:
        await get_and_cache_all_types_dict(settings_service)

    # 实现：检查类型是否存在于缓存。
    if (
        component_cache.all_types_dict
        and "components" in component_cache.all_types_dict
        and component_type in component_cache.all_types_dict["components"]
    ):
        # 实现：懒加载模式下补全该类型的所有组件。
        if settings_service.settings.lazy_load_components:
            for component_name in list(component_cache.all_types_dict["components"][component_type].keys()):
                await ensure_component_loaded(component_type, component_name, settings_service)

        return component_cache.all_types_dict["components"][component_type]

    return {}


# 注意：避免 `list` 作为不可哈希键。
def key_func(*args, **kwargs):
    """为缓存场景生成稳定 key。

    契约：将可序列化参数拼接为字符串 key。
    副作用：无。
    失败语义：传入不可序列化对象会抛出异常。
    决策：使用 JSON 序列化作为 key 基础。
    问题：需要对列表参数生成可哈希 key。
    方案：`json.dumps` 拼接参数序列。
    代价：序列化成本随参数长度增长。
    重评：当需要更高性能 key 时切换到自定义哈希。
    """
    # 注意：components_paths 是列表，无法直接作为哈希 key。
    return json.dumps(args) + json.dumps(kwargs)


async def aget_all_components(components_paths, *, as_dict=False):
    """异步获取全部组件列表或字典。

    契约：`as_dict=True` 返回以组件名为键的字典，否则返回列表。
    副作用：触发自定义组件加载。
    失败语义：加载异常由下层处理。
    决策：以 display_name 作为组件名称输出。
    问题：UI 需要可读名称而非内部标识。
    方案：将 `display_name` 复制到 `name` 字段。
    代价：可能与内部唯一标识不一致。
    重评：当 UI 支持区分显示名与标识时移除此转换。
    """
    all_types_dict = await aget_all_types_dict(components_paths)
    components = {} if as_dict else []
    for category in all_types_dict.values():
        for component in category.values():
            component["name"] = component["display_name"]
            if as_dict:
                components[component["name"]] = component
            else:
                components.append(component)
    return components


def get_all_components(components_paths, *, as_dict=False):
    """同步获取全部组件列表或字典。

    契约：`as_dict=True` 返回以组件名为键的字典，否则返回列表。
    副作用：同步构建自定义组件。
    失败语义：异常由下层处理。
    决策：同步入口仅用于不支持异步的调用场景。
    问题：部分调用场景无法使用异步 API。
    方案：提供同步包装并复用构建逻辑。
    代价：阻塞线程并增加响应时间。
    重评：当全链路异步化后逐步废弃。
    """
    # 注意：延迟导入以避免循环依赖。
    from lfx.custom.utils import build_custom_components

    all_types_dict = build_custom_components(components_paths=components_paths)
    components = [] if not as_dict else {}
    for category in all_types_dict.values():
        for component in category.values():
            component["name"] = component["display_name"]
            if as_dict:
                components[component["name"]] = component
            else:
                components.append(component)
    return components
