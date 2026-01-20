"""
模块名称：模板检索与加载

本模块从 `starter_projects` 目录读取模板 `JSON`，提供检索、精确获取与统计能力，供模板市场与示例流程入口使用。主要功能包括：
- `list_templates`：按关键字/标签检索并裁剪字段
- `get_template_by_id`：按模板 `ID` 精确获取
- `get_all_tags` / `get_templates_count`：标签与数量统计

关键组件：
- `list_templates` / `get_template_by_id`

设计背景：模板以独立 `JSON` 文件分发，读取成本可接受且易于离线部署。
注意事项：目录缺失会抛 `FileNotFoundError`；单文件解析失败会被跳过并记录日志。
"""

import json
from pathlib import Path
from typing import Any

import orjson
from lfx.log.logger import logger


def list_templates(
    query: str | None = None,
    fields: list[str] | None = None,
    tags: list[str] | None = None,
    starter_projects_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按条件检索模板并裁剪字段。

    契约：`query` 为大小写不敏感子串匹配；`tags` 为“任一匹配”；返回列表内字典字段受 `fields` 控制。
    副作用：遍历目录并读取 `JSON`；解析失败写入日志 `Failed to parse`。
    失败语义：目录不存在抛 `FileNotFoundError`；单文件解析失败被跳过。
    关键路径（三步）：1) 解析模板目录 2) 遍历并加载 `JSON` 3) 过滤与裁剪字段
    异常流：`JSON` 解码异常 -> 记录日志并继续处理其他文件。
    性能瓶颈：每次调用都会读取全部模板文件，文件数越多越慢。
    排障入口：日志关键字 `Failed to parse`。
    决策：`tags` 采用“任一匹配”语义
    问题：模板可能同时属于多个类别，调用方希望宽松命中
    方案：使用 `any(tag in template_tags for tag in tags)`
    代价：可能返回过多结果
    重评：当需要更精确筛选或引入 `AND/NOT` 语义时
    """
    if starter_projects_path:
        starter_projects_dir = Path(starter_projects_path)
    else:
        starter_projects_dir = Path(__file__).parent.parent.parent / "initial_setup" / "starter_projects"

    if not starter_projects_dir.exists():
        msg = f"Starter projects directory not found: {starter_projects_dir}"
        raise FileNotFoundError(msg)

    results = []

    for template_file in starter_projects_dir.glob("*.json"):
        try:
            with Path(template_file).open(encoding="utf-8") as f:
                template_data = json.load(f)

            if query:
                name = template_data.get("name", "").lower()
                description = template_data.get("description", "").lower()
                query_lower = query.lower()

                if query_lower not in name and query_lower not in description:
                    continue

            if tags:
                template_tags = template_data.get("tags", [])
                if not template_tags:
                    continue
                if not any(tag in template_tags for tag in tags):
                    continue

            if fields:
                filtered_data = {field: template_data.get(field) for field in fields if field in template_data}
            else:
                filtered_data = template_data

            results.append(filtered_data)

        except (json.JSONDecodeError, orjson.JSONDecodeError) as e:
            logger.warning(f"Failed to parse {template_file}: {e}")
            continue

    return results


def get_template_by_id(
    template_id: str,
    fields: list[str] | None = None,
    starter_projects_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """按模板 `ID` 获取单个模板。

    契约：`template_id` 为字符串 `ID`；命中返回模板字典，未命中返回 `None`。
    副作用：读取目录内 `JSON` 文件。
    失败语义：解码失败的文件被静默跳过；不存在时返回 `None`。
    关键路径（三步）：1) 解析目录 2) 逐文件加载 `JSON` 3) 命中 `ID` 后返回
    性能瓶颈：线性扫描模板文件，文件多时 `O(n)`。
    排障入口：无显式日志，需结合上层调用日志排查。
    决策：不构建索引，直接逐文件扫描
    问题：模板数量可控且无需维护额外索引文件
    方案：在目录内逐个 `JSON` 扫描匹配 `ID`
    代价：查找复杂度随模板数量增长
    重评：当模板数量显著增长或查找频率升高时
    """
    if starter_projects_path:
        starter_projects_dir = Path(starter_projects_path)
    else:
        starter_projects_dir = Path(__file__).parent.parent.parent / "initial_setup" / "starter_projects"

    for template_file in starter_projects_dir.glob("*.json"):
        try:
            with Path(template_file).open(encoding="utf-8") as f:
                template_data = json.load(f)

            if template_data.get("id") == template_id:
                if fields:
                    return {field: template_data.get(field) for field in fields if field in template_data}
                return template_data

        except (json.JSONDecodeError, orjson.JSONDecodeError):
            continue

    return None


def get_all_tags(starter_projects_path: str | Path | None = None) -> list[str]:
    """返回所有模板中出现的唯一标签列表。

    契约：输出为去重并排序后的标签列表。
    副作用：读取全部模板文件并记录异常日志 `Error loading template`。
    失败语义：单文件解析失败被忽略并继续。
    关键路径（三步）：1) 解析目录 2) 读取 `JSON` 3) 汇总并排序标签
    性能瓶颈：需读取全部模板文件内容。
    排障入口：日志关键字 `Error loading template`。
    决策：使用 `orjson` 加速解析
    问题：模板数量较多时标准 `json` 解析开销较高
    方案：优先使用 `orjson.loads`
    代价：引入对 `orjson` 的依赖
    重评：当解析性能不再是瓶颈或依赖需要简化时
    """
    if starter_projects_path:
        starter_projects_dir = Path(starter_projects_path)
    else:
        starter_projects_dir = Path(__file__).parent.parent.parent / "initial_setup" / "starter_projects"
    all_tags = set()

    for template_file in starter_projects_dir.glob("*.json"):
        try:
            template_data = orjson.loads(Path(template_file).read_text(encoding="utf-8"))

            tags = template_data.get("tags", [])
            all_tags.update(tags)

        except (json.JSONDecodeError, orjson.JSONDecodeError) as e:
            logger.aexception(f"Error loading template {template_file}: {e}")
            continue

    return sorted(all_tags)


def get_templates_count(starter_projects_path: str | Path | None = None) -> int:
    """统计模板文件数量。

    契约：返回目录内 `*.json` 文件数量。
    副作用：读取目录结构。
    失败语义：目录不存在时返回 `0`（由 `glob` 自然结果决定）。
    性能瓶颈：目录遍历成本随文件数线性增长。
    排障入口：无显式日志。
    决策：实时遍历而非缓存数量
    问题：模板数量变化不频繁但需即时反映
    方案：每次调用执行 `glob("*.json")`
    代价：频繁调用会重复遍历目录
    重评：当调用频率升高或目录极大时
    """
    if starter_projects_path:
        starter_projects_dir = Path(starter_projects_path)
    else:
        starter_projects_dir = Path(__file__).parent.parent.parent / "initial_setup" / "starter_projects"
    return len(list(starter_projects_dir.glob("*.json")))
