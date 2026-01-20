"""模块名称：Mustache 模板安全校验

模块目的：限制模板语法以降低注入风险。
主要功能：
- 拦截三花括号、段落/偏移/注释等高风险语法
- 仅允许 `{{variable}}` 形式的简单变量
使用场景：用户可编辑模板、运行时渲染场景。
关键组件：`validate_mustache_template`、`safe_mustache_render`
设计背景：模板输入可能来自用户，需要最小可用子集。
注意事项：只进行单次替换，不支持点号访问或二次渲染。
"""

import re
from typing import Any

# 注意：与前端规则一致，仅允许简单变量名
SIMPLE_VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")

# 需要阻断的复杂语法模式
DANGEROUS_PATTERNS = [
    re.compile(r"\{\{\{"),  # 三花括号（不转义 HTML）
    re.compile(r"\{\{#"),  # 段落/条件起始
    re.compile(r"\{\{/"),  # 段落/条件结束
    re.compile(r"\{\{\^"),  # 反向段落
    re.compile(r"\{\{&"),  # 不转义变量
    re.compile(r"\{\{>"),  # `partials`（模板片段）
    re.compile(r"\{\{!"),  # 注释
    re.compile(r"\{\{\."),  # 当前上下文访问
]


def validate_mustache_template(template: str) -> None:
    """校验模板仅包含简单变量替换。

    失败语义：检测到复杂语法时抛 `ValueError`。
    """
    if not template:
        return

    # 安全：检测高风险语法
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(template):
            msg = (
                "Complex mustache syntax is not allowed. Only simple variable substitution "
                "like {{variable}} is permitted."
            )
            raise ValueError(msg)

    # 校验所有 `{{ }}` 都是简单变量
    all_mustache_patterns = re.findall(r"\{\{[^}]*\}\}", template)
    for pattern in all_mustache_patterns:
        if not SIMPLE_VARIABLE_PATTERN.match(pattern):
            msg = f"Invalid mustache variable: {pattern}. Only simple variable names like {{{{variable}}}} are allowed."
            raise ValueError(msg)


def safe_mustache_render(template: str, variables: dict[str, Any]) -> str:
    """安全渲染模板（仅简单变量一次替换）。

    关键路径：
    1) 校验模板安全规则
    2) 提取简单变量
    3) 单次替换生成结果

    契约：变量值中出现 `{{...}}` 不会被二次渲染，防止注入。
    失败语义：模板包含复杂语法时抛 `ValueError`。
    """
    # 先做模板安全校验
    validate_mustache_template(template)

    # 仅替换简单变量名
    def replace_variable(match):
        var_name = match.group(1)

        # 注意：不支持点号路径
        value = variables.get(var_name, "")

        # 统一转换为字符串
        return str(value) if value is not None else ""

    # 替换全部简单变量
    return SIMPLE_VARIABLE_PATTERN.sub(replace_variable, template)
