"""
模块名称：Python 函数组件（原型）

本模块提供直接执行用户自定义 Python 函数的原型组件，支持返回 Data 或 Message。
主要功能：
- 解析并构造可调用函数对象；
- 执行函数并包装为 Data/Message 输出。

关键组件：
- PythonFunctionComponent：函数执行组件。

设计背景：用于原型验证与快速扩展，但存在执行任意代码风险。
注意事项：执行用户代码具有安全与稳定性风险，应限制使用范围。
"""

from collections.abc import Callable

from lfx.custom.custom_component.component import Component
from lfx.custom.utils import get_function
from lfx.io import CodeInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message


class PythonFunctionComponent(Component):
    """Python 函数执行组件

    契约：输入 `function_code`；输出 callable/Data/Message。
    关键路径：1) 解析函数 2) 执行函数 3) 包装输出。
    决策：允许执行任意函数以支持快速原型
    问题：用户需要在流程中注入自定义逻辑
    方案：提供 CodeInput 并运行 `get_function`
    代价：存在执行任意代码风险
    重评：当引入沙箱或受限执行环境后
    """
    display_name = "Python Function"
    description = "Define and execute a Python function that returns a Data object or a Message."
    icon = "Python"
    name = "PythonFunction"
    legacy = True

    inputs = [
        CodeInput(
            name="function_code",
            display_name="Function Code",
            info="The code for the function.",
        ),
    ]

    outputs = [
        Output(
            name="function_output",
            display_name="Function Callable",
            method="get_function_callable",
        ),
        Output(
            name="function_output_data",
            display_name="Function Output (Data)",
            method="execute_function_data",
        ),
        Output(
            name="function_output_str",
            display_name="Function Output (Message)",
            method="execute_function_message",
        ),
    ]

    def get_function_callable(self) -> Callable:
        """返回可调用函数对象

        契约：返回 `Callable`；副作用：更新 `self.status` 为源码文本。
        关键路径：1) 读取代码 2) 解析函数 3) 返回 callable。
        异常流：解析失败由 `get_function` 抛出异常。
        """
        function_code = self.function_code
        self.status = function_code
        return get_function(function_code)

    def execute_function(self) -> list[dotdict | str] | dotdict | str:
        """执行用户函数并返回原始结果

        契约：返回函数结果或错误字符串；空代码直接返回提示。
        关键路径：1) 校验代码 2) 解析函数 3) 执行并返回。
        异常流：捕获所有异常并返回错误文本，避免组件崩溃。
        排障入口：日志 `Error executing function`。
        决策：以字符串形式返回错误信息
        问题：执行失败时需要在 UI 中可见
        方案：捕获异常并返回错误字符串
        代价：错误类型信息丢失为文本
        重评：当需要结构化错误输出时
        """
        function_code = self.function_code

        if not function_code:
            return "No function code provided."

        try:
            func = get_function(function_code)
            return func()
        except Exception as e:  # noqa: BLE001  # 注意：原型组件允许捕获所有异常以避免崩溃。
            logger.debug("Error executing function", exc_info=True)
            return f"Error executing function: {e}"

    def execute_function_data(self) -> list[Data]:
        """将执行结果包装为 Data 列表

        契约：始终返回 `list[Data]`；字符串结果转换为 `Data(text=...)`。
        关键路径：1) 执行函数 2) 统一为列表 3) 构造 Data。
        """
        results = self.execute_function()
        results = results if isinstance(results, list) else [results]
        return [(Data(text=x) if isinstance(x, str) else Data(**x)) for x in results]

    def execute_function_message(self) -> Message:
        """将执行结果拼接为 Message 文本

        契约：返回 `Message`；结果逐条转换为字符串并用换行拼接。
        关键路径：1) 执行函数 2) 归一化为列表 3) 拼接文本。
        """
        results = self.execute_function()
        results = results if isinstance(results, list) else [results]
        results_list = [str(x) for x in results]
        results_str = "\n".join(results_list)
        return Message(text=results_str)
