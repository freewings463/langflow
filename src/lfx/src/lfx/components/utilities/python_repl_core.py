"""
模块名称：Python REPL 组件

本模块提供受限导入的 Python 代码执行能力，主要用于在流程中运行轻量脚本与验证逻辑。主要功能包括：
- 根据允许列表导入模块并构建全局命名空间
- 使用 `LangChain` 的 `PythonREPL` 执行代码
- 将输出封装为 `Data` 返回

关键组件：
- `PythonREPLComponent`：组件主体
- `get_globals`：构建允许导入的全局变量
- `run_python_repl`：执行代码并返回结果

设计背景：提供可配置的 Python 执行入口，降低外部脚本接入成本。
使用场景：简单计算、文本处理或快速原型验证。
注意事项：执行代码具有安全风险，仅适用于可信输入；导入白名单不等同沙箱隔离。
"""

import importlib

from langchain_experimental.utilities import PythonREPL

from lfx.custom.custom_component.component import Component
from lfx.io import MultilineInput, Output, StrInput
from lfx.schema.data import Data


class PythonREPLComponent(Component):
    """Python 代码执行组件。

    契约：输入 `global_imports` 与 `python_code`；输出 `Data`（`result` 或 `error`）。
    副作用：执行用户提供代码；记录执行日志。
    失败语义：导入/语法/运行时错误会返回 `error` 字段。
    决策：使用 `PythonREPL` 而非直接 `exec`。
    问题：需要与 LangChain 生态一致的执行与输出格式。
    方案：复用 `langchain_experimental` 的 REPL 实现。
    代价：功能受 REPL 限制，且安全风险仍需外部控制。
    重评：当需要更强沙箱或资源限制时更换执行引擎。
    """
    display_name = "Python Interpreter"
    description = "Run Python code with optional imports. Use print() to see the output."
    documentation: str = "https://docs.langflow.org/python-interpreter"
    icon = "square-terminal"

    inputs = [
        StrInput(
            name="global_imports",
            display_name="Global Imports",
            info="A comma-separated list of modules to import globally, e.g. 'math,numpy,pandas'.",
            value="math,pandas",
            required=True,
        ),
        MultilineInput(
            name="python_code",
            display_name="Python Code",
            info="The Python code to execute. Only modules specified in Global Imports can be used.",
            value="print('Hello, World!')",
            input_types=["Message"],
            tool_mode=True,
            required=True,
        ),
    ]

    outputs = [
        Output(
            display_name="Results",
            name="results",
            type_=Data,
            method="run_python_repl",
        ),
    ]

    def get_globals(self, global_imports: str | list[str]) -> dict:
        """构建受限全局命名空间。

        契约：`global_imports` 支持逗号分隔字符串或列表；返回可用于 REPL 的 `globals` 字典。
        副作用：导入模块并写入全局字典。
        失败语义：模块导入失败抛 `ImportError`；类型不正确抛 `TypeError`。
        关键路径（三步）：1) 解析导入列表 2) 逐个导入模块 3) 返回全局字典。
        决策：只导入白名单模块而非暴露完整 `globals()`。
        问题：限制可访问模块以降低误用风险。
        方案：按输入列表逐个导入并注入全局。
        代价：仍无法阻止白名单内模块的敏感操作。
        重评：当需要更严格隔离时引入沙箱或容器。
        """
        global_dict = {}

        try:
            if isinstance(global_imports, str):
                modules = [module.strip() for module in global_imports.split(",")]
            elif isinstance(global_imports, list):
                modules = global_imports
            else:
                msg = "global_imports must be either a string or a list"
                raise TypeError(msg)

            for module in modules:
                try:
                    imported_module = importlib.import_module(module)
                    global_dict[imported_module.__name__] = imported_module
                except ImportError as e:
                    msg = f"Could not import module {module}: {e!s}"
                    raise ImportError(msg) from e

        except Exception as e:
            self.log(f"Error in global imports: {e!s}")
            raise
        else:
            self.log(f"Successfully imported modules: {list(global_dict.keys())}")
            return global_dict

    def run_python_repl(self) -> Data:
        """执行 Python 代码并返回结果。

        契约：使用 `get_globals` 生成的命名空间执行 `python_code`；返回 `result` 或 `error`。
        副作用：执行任意代码（存在安全风险），并记录运行日志。
        关键路径（三步）：1) 构建允许导入的 globals 2) 执行 REPL 3) 归一化输出。
        异常流：导入、语法或运行时错误会转为 `error` 返回。
        决策：错误统一转为 `Data.error` 而非抛异常。
        问题：组件在流程中需要可显示的失败结果。
        方案：捕获常见异常并返回错误文本。
        代价：调用方可能忽略错误继续执行。
        重评：当流程需要强失败语义时改为抛异常。
        """
        try:
            globals_ = self.get_globals(self.global_imports)
            python_repl = PythonREPL(_globals=globals_)
            result = python_repl.run(self.python_code)
            result = result.strip() if result else ""

            self.log("Code execution completed successfully")
            return Data(data={"result": result})

        except ImportError as e:
            error_message = f"Import Error: {e!s}"
            self.log(error_message)
            return Data(data={"error": error_message})

        except SyntaxError as e:
            error_message = f"Syntax Error: {e!s}"
            self.log(error_message)
            return Data(data={"error": error_message})

        except (NameError, TypeError, ValueError) as e:
            error_message = f"Error during execution: {e!s}"
            self.log(error_message)
            return Data(data={"error": error_message})

    def build(self):
        """返回组件主执行函数入口。"""
        return self.run_python_repl
