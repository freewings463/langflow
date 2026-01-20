"""
模块名称：CLI 校验工具

本模块提供 CLI 运行前的校验逻辑，主要用于在无数据库模式下校验全局变量命名。主要功能包括：
- 校验环境变量名格式
- 扫描图中需要从环境加载的字段并产出错误列表

关键组件：
- `is_valid_env_var_name`
- `validate_global_variables_for_env`

设计背景：在 noop 模式下依赖环境变量，需提前拦截不合法命名。
注意事项：仅在 `use_noop_database=True` 时触发校验。
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lfx.graph.graph.base import Graph


def is_valid_env_var_name(name: str) -> bool:
    """判断字符串是否为合法环境变量名。

    契约：以字母或下划线开头，仅包含字母/数字/下划线。
    失败语义：无。
    副作用：无。
    """
    # 注意：仅允许字母/数字/下划线，且首字符不能为数字
    pattern = r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    return bool(re.match(pattern, name))


def validate_global_variables_for_env(graph: "Graph") -> list[str]:
    """校验需要从环境变量加载的全局变量名称。

    契约：仅在 noop 模式下校验；返回错误列表，空列表表示通过。
    失败语义：无（仅收集错误）。
    副作用：读取 settings 服务配置。
    """
    from lfx.services.deps import get_settings_service

    errors = []
    settings_service = get_settings_service()

    is_noop_mode = settings_service and settings_service.settings.use_noop_database

    if not is_noop_mode:
        return errors

    for vertex in graph.vertices:
        load_from_db_fields = getattr(vertex, "load_from_db_fields", [])

        for field_name in load_from_db_fields:
            field_value = vertex.params.get(field_name)

            if field_value and isinstance(field_value, str) and not is_valid_env_var_name(field_value):
                errors.append(
                    f"Component '{vertex.display_name}' (id: {vertex.id}) has field '{field_name}' "
                    f"with value '{field_value}' that contains invalid characters for an environment "
                    f"variable name. Environment variable names must start with a letter or underscore "
                    f"and contain only letters, numbers, and underscores (no spaces or special characters)."
                )

    return errors
