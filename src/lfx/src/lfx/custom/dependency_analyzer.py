"""
模块名称：自定义组件依赖分析

本模块基于 AST 解析组件代码中的 import 语句，输出第三方依赖列表与版本信息。
主要功能：
- 识别绝对/相对导入并过滤标准库；
- 可选解析已安装包的版本；
- 输出依赖统计结果供前端展示。

设计背景：自定义组件加载时需要提示外部依赖与版本信息。
注意事项：版本解析依赖本地环境已安装包，未安装时返回 None。
"""

from __future__ import annotations

import ast
import importlib.metadata as md
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache

try:
    STDLIB_MODULES: set[str] = set(sys.stdlib_module_names)  # 3.10+
except AttributeError:
    # 注意：在 <3.10 环境下使用内置模块列表作为回退。
    STDLIB_MODULES = set(sys.builtin_module_names)


@dataclass(frozen=True)
class DependencyInfo:
    """记录代码中导入依赖的信息。"""

    name: str  # 注意：包名（如 "numpy", "requests"）。
    version: str | None  # 注意：已安装包版本（可选）。
    is_local: bool  # 注意：相对导入标记（如 from .module import ...）。


def _top_level(pkg: str) -> str:
    """提取顶层包名（如 numpy.linalg -> numpy）。"""
    return pkg.split(".", 1)[0]


def _is_relative(module: str | None) -> bool:
    """判断是否为相对导入（以 '.' 开头）。"""
    return module is not None and module.startswith(".")


class _ImportVisitor(ast.NodeVisitor):
    """AST 访问器：提取 import 依赖信息。"""

    def __init__(self):
        self.results: list[DependencyInfo] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            full = alias.name
            dep = DependencyInfo(
                name=_top_level(full),
                version=None,
                is_local=False,  # 注意：普通 import 视为非本地。
            )
            self.results.append(dep)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        # 注意：按相对导入规则重建完整模块名。
        if node.level > 0:
            # 注意：相对导入示例：from .module import x / from ..parent import x
            dots = "." * node.level
            full_module = dots + (node.module or "")
        else:
            # 注意：绝对导入示例：from module import x
            full_module = node.module or ""
        for _alias in node.names:
            dep = DependencyInfo(
                name=_top_level(full_module.lstrip(".")) if full_module else "",
                version=None,
                is_local=_is_relative(full_module),  # 注意：标记是否为相对导入。
            )
            self.results.append(dep)


def _classify_dependency(dep: DependencyInfo) -> DependencyInfo:
    """补充外部依赖版本信息（若可解析）。"""
    version = None
    if not dep.is_local and dep.name:
        version = _get_distribution_version(dep.name)

    return DependencyInfo(
        name=dep.name,
        version=version,
        is_local=dep.is_local,
    )


def analyze_dependencies(source: str, *, resolve_versions: bool = True) -> list[dict]:
    """分析代码依赖并返回字典列表

    契约：返回依赖字典列表，包含 name/version/is_local 等信息。
    关键路径：1) 解析 AST 2) 访问 import 3) 去重并过滤标准库。
    异常流：语法错误由上层处理（此处不捕获）。
    """
    code = source

    # 实现：解析代码并提取 import 语句。
    tree = ast.parse(code)
    visitor = _ImportVisitor()
    visitor.visit(tree)

    # 实现：按包名去重依赖。
    unique_packages: dict[str, DependencyInfo] = {}
    for raw_dep in visitor.results:
        processed_dep = _classify_dependency(raw_dep) if resolve_versions else raw_dep

        # 注意：忽略标准库与本地导入，仅保留外部依赖。
        if processed_dep.name in STDLIB_MODULES or processed_dep.is_local:
            continue

        # 注意：仅按包名去重（不区分子模块）。
        if processed_dep.name not in unique_packages:
            unique_packages[processed_dep.name] = processed_dep

    return [asdict(d) for d in unique_packages.values()]


def analyze_component_dependencies(component_code: str) -> dict:
    """分析组件依赖并返回统计结果

    契约：返回 `{"total_dependencies": int, "dependencies": [...]}`。
    异常流：解析失败返回空依赖。
    """
    try:
        deps = analyze_dependencies(component_code, resolve_versions=True)

        return {
            "total_dependencies": len(deps),
            "dependencies": [{"name": d["name"], "version": d["version"]} for d in deps if d["name"]],
        }
    except (SyntaxError, TypeError, ValueError, ImportError):
        # 注意：分析失败返回最小依赖信息。
        return {
            "total_dependencies": 0,
            "dependencies": [],
        }


# 注意：packages_distributions 代价较高，使用全局缓存。
@lru_cache(maxsize=1)
def _get_packages_distributions():
    """缓存 packages_distributions() 调用结果。"""
    try:
        return md.packages_distributions()
    except (OSError, AttributeError, ValueError):
        return {}


# 注意：版本查询频繁，缓存单包版本。
@lru_cache(maxsize=128)
def _get_distribution_version(import_name: str):
    """根据导入名反查分发包版本。"""
    try:
        # 注意：反向查找提供该导入名的分发包。
        reverse_map = _get_packages_distributions()
        dist_names = reverse_map.get(import_name)
        if not dist_names:
            return None

        # 注意：取首个匹配分发包版本。
        dist_name = dist_names[0]
        return md.distribution(dist_name).version
    except (ImportError, AttributeError, OSError, ValueError):
        return None
