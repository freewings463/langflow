"""
模块名称：基础计算工具组件

本模块提供安全的算术表达式求值能力，供低代码工具调用使用。
主要功能包括：
- 使用 `ast` 解析表达式并限制可执行节点
- 统一格式化输出并返回结构化结果
- 将异常转换为可展示的错误信息

关键组件：
- `CalculatorToolComponent._eval_expr`：受限 AST 求值
- `CalculatorToolComponent._evaluate_expression`：格式化输出与错误处理

设计背景：需要一个不依赖 `eval` 的安全计算工具。
注意事项：仅支持 `+ - * / **` 与一元运算，不支持函数调用。
"""

import ast
import operator

import pytest
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import MessageTextInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class CalculatorToolComponent(LCToolComponent):
    """安全算术计算工具组件。

    契约：输入表达式字符串，输出 `result` 或错误信息的 `Data` 列表。
    决策：采用 AST 白名单解释器而非 `eval`。
    问题：直接 `eval` 存在代码执行风险。
    方案：仅允许数字与基本运算节点，拒绝函数调用。
    代价：不支持高级数学函数与变量。
    重评：当需要扩展函数库时引入受控函数映射。
    """
    display_name = "Calculator"
    description = "Perform basic arithmetic operations on a given expression."
    icon = "calculator"
    name = "CalculatorTool"
    legacy = True
    replacement = ["helpers.CalculatorComponent"]

    inputs = [
        MessageTextInput(
            name="expression",
            display_name="Expression",
            info="The arithmetic expression to evaluate (e.g., '4*4*(33/22)+12-20').",
        ),
    ]

    class CalculatorToolSchema(BaseModel):
        expression: str = Field(..., description="The arithmetic expression to evaluate.")

    def run_model(self) -> list[Data]:
        """执行表达式求值并返回结果。"""
        return self._evaluate_expression(self.expression)

    def build_tool(self) -> Tool:
        """构建可被 LangChain 调用的结构化工具。"""
        try:
            from langchain.tools import StructuredTool
        except Exception:  # noqa: BLE001
            pytest.skip("langchain is not available")

        return StructuredTool.from_function(
            name="calculator",
            description="Evaluate basic arithmetic expressions. Input should be a string containing the expression.",
            func=self._eval_expr_with_error,
            args_schema=self.CalculatorToolSchema,
        )

    def _eval_expr(self, node):
        """递归求值 AST 节点，限制为安全的算术节点。

        关键路径（三步）：
        1) 识别节点类型（数字/二元/一元）
        2) 递归计算子节点
        3) 拒绝不支持节点并抛错
        """
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.BinOp):
            left_val = self._eval_expr(node.left)
            right_val = self._eval_expr(node.right)
            return self.operators[type(node.op)](left_val, right_val)
        if isinstance(node, ast.UnaryOp):
            operand_val = self._eval_expr(node.operand)
            return self.operators[type(node.op)](operand_val)
        if isinstance(node, ast.Call):
            # 安全：显式禁止函数调用，避免执行任意代码。
            msg = (
                "Function calls like sqrt(), sin(), cos() etc. are not supported. "
                "Only basic arithmetic operations (+, -, *, /, **) are allowed."
            )
            raise TypeError(msg)
        msg = f"Unsupported operation or expression type: {type(node).__name__}"
        raise TypeError(msg)

    def _eval_expr_with_error(self, expression: str) -> list[Data]:
        """将异常包装为 `ToolException`，供工具调用使用。"""
        try:
            return self._evaluate_expression(expression)
        except Exception as e:
            raise ToolException(str(e)) from e

    def _evaluate_expression(self, expression: str) -> list[Data]:
        """解析并计算表达式，返回结构化结果。

        关键路径（三步）：
        1) 解析表达式为 AST
        2) 使用受限求值器计算
        3) 格式化输出并返回
        失败语义：语法错误/类型错误返回 `error` 字段；除零单独提示。
        """
        try:
            # 实现：解析 AST 并交由受限求值器计算。
            tree = ast.parse(expression, mode="eval")
            result = self._eval_expr(tree.body)

            # 注意：统一保留 6 位小数并去掉尾随 0，便于前端展示。
            formatted_result = f"{result:.6f}".rstrip("0").rstrip(".")

            self.status = formatted_result
            return [Data(data={"result": formatted_result})]

        except (SyntaxError, TypeError, KeyError) as e:
            error_message = f"Invalid expression: {e}"
            self.status = error_message
            return [Data(data={"error": error_message, "input": expression})]
        except ZeroDivisionError:
            error_message = "Error: Division by zero"
            self.status = error_message
            return [Data(data={"error": error_message, "input": expression})]
        except Exception as e:  # noqa: BLE001
            logger.debug("Error evaluating expression", exc_info=True)
            error_message = f"Error: {e}"
            self.status = error_message
            return [Data(data={"error": error_message, "input": expression})]

    def __init__(self, *args, **kwargs):
        """初始化运算符映射表。"""
        super().__init__(*args, **kwargs)
        self.operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
        }
