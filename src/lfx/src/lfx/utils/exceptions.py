"""模块名称：异常消息格式化

模块目的：统一异常信息的提取与前端展示格式。
主要功能：
- 语法错误行号与上下文摘要
- 链式异常根因追踪
使用场景：前端错误提示与日志聚合展示。
关键组件：`format_syntax_error_message`、`format_exception_message`
设计背景：需要稳定可读的错误输出，避免暴露内部堆栈细节。
注意事项：`get_causing_exception` 会递归查找 `__cause__`。
"""

def format_syntax_error_message(exc: SyntaxError) -> str:
    """格式化语法错误消息供前端展示。

    契约：若 `exc.text` 缺失，仅返回行号。
    """
    if exc.text is None:
        return f"Syntax error in code. Error on line {exc.lineno}"
    return f"Syntax error in code. Error on line {exc.lineno}: {exc.text.strip()}"


def get_causing_exception(exc: BaseException) -> BaseException:
    """递归获取异常链中的根因异常。"""
    if hasattr(exc, "__cause__") and exc.__cause__:
        return get_causing_exception(exc.__cause__)
    return exc


def format_exception_message(exc: Exception) -> str:
    """统一异常消息的前端格式。

    决策：优先返回语法错误的格式化信息，避免被上层包装信息覆盖。
    问题：链式异常会丢失具体语法错误位置。
    方案：递归解析 `__cause__`，若为 `SyntaxError` 则使用专用格式。
    代价：需要遍历异常链，增加少量开销。
    重评：若未来异常链深度显著增加，再考虑限深或迭代实现。
    """
    causing_exception = get_causing_exception(exc)
    if isinstance(causing_exception, SyntaxError):
        return format_syntax_error_message(causing_exception)
    return str(exc)
