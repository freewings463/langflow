"""
模块名称：AST 树访问器（必填输入推断）

本模块提供基于 AST 的访问器，用于推断组件代码中被访问的必填输入。
主要功能：
- 扫描 `self.<input>` 属性访问；
- 将 required 输入记录为必填字段集合。

设计背景：静态分析组件代码，辅助推断必填输入。
注意事项：仅基于语法树推断，无法识别运行时动态访问。
"""

import ast
from typing import Any

from typing_extensions import override


class RequiredInputsVisitor(ast.NodeVisitor):
    """AST 访问器：收集必填输入字段。"""
    def __init__(self, inputs: dict[str, Any]):
        self.inputs: dict[str, Any] = inputs
        self.required_inputs: set[str] = set()

    @override
    def visit_Attribute(self, node) -> None:
        """捕获 self.<input> 的属性访问并标记必填字段。"""
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in self.inputs
            and self.inputs[node.attr].required
        ):
            self.required_inputs.add(node.attr)
        self.generic_visit(node)
