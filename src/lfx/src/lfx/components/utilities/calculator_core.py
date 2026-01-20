"""
模块名称：表达式计算器组件

本模块提供安全的基础算术计算能力，主要用于在流程中解析并计算简单数学表达式。主要功能包括：
- 解析表达式为 `AST` 并进行白名单计算
- 将计算结果格式化为可读字符串
- 以 `Data` 形式返回结果或错误信息

关键组件：
- `CalculatorComponent`：组件主体
- `_eval_expr`：递归计算 AST 节点
- `evaluate_expression`：表达式解析与错误处理入口

设计背景：避免使用 `eval`，仅允许受控的算术运算。
使用场景：在流程中对输入表达式进行基础计算。
注意事项：仅支持加减乘除与乘方；非法表达式会返回错误信息而非抛出异常。
"""

import ast
import operator
from collections.abc import Callable

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MessageTextInput
from lfx.io import Output
from lfx.schema.data import Data


class CalculatorComponent(Component):
    """基础算术计算组件。

    契约：输入 `expression` 字符串；输出 `Data`，包含 `result` 或 `error`。
    副作用：记录计算结果到日志，并更新 `self.status`。
    失败语义：除零、语法错误或不支持的操作会返回错误信息（不抛异常）。
    关键路径：1) 解析表达式 `AST` 2) 递归计算 3) 格式化并返回结果。
    决策：使用 AST 白名单而非 `eval`。
    问题：直接 `eval` 存在执行任意代码风险。
    方案：仅允许算术运算节点并显式映射运算符。
    代价：不支持函数调用或变量表达式。
    重评：当需要更复杂表达式且具备安全沙箱时。
    """
    display_name = "Calculator"
    description = "Perform basic arithmetic operations on a given expression."
    documentation: str = "https://docs.langflow.org/calculator"
    icon = "calculator"

    # 安全：仅允许白名单运算符，避免执行任意表达式。
    OPERATORS: dict[type[ast.operator], Callable] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }

    inputs = [
        MessageTextInput(
            name="expression",
            display_name="Expression",
            info="The arithmetic expression to evaluate (e.g., '4*4*(33/22)+12-20').",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Data", name="result", type_=Data, method="evaluate_expression"),
    ]

    def _eval_expr(self, node: ast.AST) -> float:
        """递归计算 AST 节点并返回浮点结果。

        契约：仅接受常量与二元运算节点；返回 `float`。
        失败语义：遇到不支持的常量类型或运算符时抛 `TypeError`。
        """
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int | float):
                return float(node.value)
            error_msg = f"Unsupported constant type: {type(node.value).__name__}"
            raise TypeError(error_msg)
        if isinstance(node, ast.Num):  # For backwards compatibility
            if isinstance(node.n, int | float):
                return float(node.n)
            error_msg = f"Unsupported number type: {type(node.n).__name__}"
            raise TypeError(error_msg)

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self.OPERATORS:
                error_msg = f"Unsupported binary operator: {op_type.__name__}"
                raise TypeError(error_msg)

            left = self._eval_expr(node.left)
            right = self._eval_expr(node.right)
            return self.OPERATORS[op_type](left, right)

        error_msg = f"Unsupported operation or expression type: {type(node).__name__}"
        raise TypeError(error_msg)

    def evaluate_expression(self) -> Data:
        """解析并计算表达式，返回 `Data` 结果。

        契约：输入 `expression` 为算术表达式；输出 `result` 或 `error`。
        副作用：写日志并更新 `self.status`。
        关键路径（三步）：1) AST 解析 2) 递归计算 3) 格式化输出。
        异常流：除零、语法错误、溢出等被捕获并返回错误信息。
        决策：将错误转为 `Data.error` 而非抛异常。
        问题：组件在流程中应返回可显示错误而不是终止。
        方案：捕获常见异常并写入 `status`/`Data`。
        代价：调用方可能忽略错误而继续流转。
        重评：当流程需要强失败语义时改为抛异常。
        """
        try:
            tree = ast.parse(self.expression, mode="eval")
            result = self._eval_expr(tree.body)

            formatted_result = f"{float(result):.6f}".rstrip("0").rstrip(".")
            self.log(f"Calculation result: {formatted_result}")

            self.status = formatted_result
            return Data(data={"result": formatted_result})

        except ZeroDivisionError:
            error_message = "Error: Division by zero"
            self.status = error_message
            return Data(data={"error": error_message, "input": self.expression})

        except (SyntaxError, TypeError, KeyError, ValueError, AttributeError, OverflowError) as e:
            error_message = f"Invalid expression: {e!s}"
            self.status = error_message
            return Data(data={"error": error_message, "input": self.expression})

    def build(self):
        """返回可执行函数入口，供框架调用。"""
        return self.evaluate_expression
