"""
模块名称：`Python` REPL 工具组件

本模块提供可执行 Python 代码的 REPL 工具，用于快速验证与脚本运行。
主要功能包括：
- 按需导入全局模块并注入执行环境
- 运行用户输入代码并返回结果
- 生成 LangChain 结构化工具

关键组件：
- `PythonREPLToolComponent.get_globals`：构造全局导入字典
- `PythonREPLToolComponent.build_tool`：创建 REPL 工具

设计背景：低代码流程需要快速执行辅助脚本。
注意事项：执行任意代码存在风险，仅适用于受控环境。
"""

import importlib

from langchain.tools import StructuredTool
from langchain_core.tools import ToolException
from langchain_experimental.utilities import PythonREPL
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import StrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class PythonREPLToolComponent(LCToolComponent):
    """Python REPL 工具组件。

    契约：输入代码字符串，返回执行结果文本。
    决策：使用 `langchain_experimental` 的 REPL 实现。
    问题：自建 REPL 难以维护并缺少生态兼容。
    方案：复用 LangChain 的实验实现并封装为结构化工具。
    代价：依赖实验包接口可能变动。
    重评：当官方 REPL 稳定接口出现时迁移。
    """
    display_name = "Python REPL"
    description = "A tool for running Python code in a REPL environment."
    name = "PythonREPLTool"
    icon = "Python"
    legacy = True
    replacement = ["processing.PythonREPLComponent"]

    inputs = [
        StrInput(
            name="name",
            display_name="Tool Name",
            info="The name of the tool.",
            value="python_repl",
        ),
        StrInput(
            name="description",
            display_name="Tool Description",
            info="A description of the tool.",
            value="A Python shell. Use this to execute python commands. "
            "Input should be a valid python command. "
            "If you want to see the output of a value, you should print it out with `print(...)`.",
        ),
        StrInput(
            name="global_imports",
            display_name="Global Imports",
            info="A comma-separated list of modules to import globally, e.g. 'math,numpy'.",
            value="math",
        ),
        StrInput(
            name="code",
            display_name="Python Code",
            info="The Python code to execute.",
            value="print('Hello, World!')",
        ),
    ]

    class PythonREPLSchema(BaseModel):
        code: str = Field(..., description="The Python code to execute.")

    def get_globals(self, global_imports: str | list[str]) -> dict:
        """构建 REPL 的全局导入字典。

        关键路径（三步）：
        1) 解析输入为模块列表
        2) 逐个导入模块
        3) 组装为全局字典
        异常流：导入失败抛 `ImportError`。
        """
        global_dict = {}
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
                msg = f"Could not import module {module}"
                raise ImportError(msg) from e
        return global_dict

    def build_tool(self) -> Tool:
        """构建可执行 REPL 工具实例。

        关键路径（三步）：
        1) 初始化 REPL 全局环境
        2) 包装执行函数并处理异常
        3) 构建结构化工具
        """
        globals_ = self.get_globals(self.global_imports)
        python_repl = PythonREPL(_globals=globals_)

        def run_python_code(code: str) -> str:
            try:
                return python_repl.run(code)
            except Exception as e:
                logger.debug("Error running Python code", exc_info=True)
                raise ToolException(str(e)) from e

        tool = StructuredTool.from_function(
            name=self.name,
            description=self.description,
            func=run_python_code,
            args_schema=self.PythonREPLSchema,
        )

        self.status = f"Python REPL Tool created with global imports: {self.global_imports}"
        return tool

    def run_model(self) -> list[Data]:
        """执行代码并返回结构化结果。"""
        tool = self.build_tool()
        result = tool.run(self.code)
        return [Data(data={"result": result})]
