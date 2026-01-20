"""
模块名称：`Python` 结构化工具生成器

本模块将用户提供的 Python 代码解析为结构化工具，支持动态参数与类型推断。
主要功能包括：
- 解析代码中的函数与类定义
- 根据函数签名生成 `StructuredTool` 参数 schema
- 注入全局变量并执行用户代码

关键组件：
- `PythonCodeStructuredTool.update_build_config`：解析代码并更新前端模板
- `PythonCodeStructuredTool.build_tool`：生成可执行工具
- `_parse_code`/`_find_imports`：抽取函数、类与导入

设计背景：低代码环境需要把用户函数直接转为工具能力。
注意事项：该组件会执行用户代码，仅适用于受控环境。
"""

import ast
import json
from typing import Any

from langchain.agents import Tool
from langchain_core.tools import StructuredTool
from pydantic.v1 import Field, create_model
from pydantic.v1.fields import Undefined
from typing_extensions import override

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.inputs.inputs import BoolInput, DropdownInput, FieldTypes, HandleInput, MessageTextInput, MultilineInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class PythonCodeStructuredTool(LCToolComponent):
    """将 Python 代码转换为结构化工具的组件。

    契约：输入工具代码、函数名与描述，输出 `StructuredTool` 实例。
    决策：使用 AST 解析函数签名并动态构造 schema。
    问题：手工维护参数描述易出错且不一致。
    方案：从代码中抽取参数与注解，并与前端配置联动。
    代价：解析与执行过程复杂，且存在执行风险。
    重评：当需要更强安全隔离时改用沙箱或受限执行。
    """
    DEFAULT_KEYS = [
        "code",
        "_type",
        "text_key",
        "tool_code",
        "tool_name",
        "tool_description",
        "return_direct",
        "tool_function",
        "global_variables",
        "_classes",
        "_functions",
    ]
    display_name = "Python Code Structured"
    description = "structuredtool dataclass code to tool"
    documentation = "https://python.langchain.com/docs/modules/tools/custom_tools/#structuredtool-dataclass"
    name = "PythonCodeStructuredTool"
    icon = "Python"
    field_order = ["name", "description", "tool_code", "return_direct", "tool_function"]
    legacy: bool = True
    replacement = ["processing.PythonREPLComponent"]

    inputs = [
        MultilineInput(
            name="tool_code",
            display_name="Tool Code",
            info="Enter the dataclass code.",
            placeholder="def my_function(args):\n    pass",
            required=True,
            real_time_refresh=True,
            refresh_button=True,
        ),
        MessageTextInput(
            name="tool_name",
            display_name="Tool Name",
            info="Enter the name of the tool.",
            required=True,
        ),
        MessageTextInput(
            name="tool_description",
            display_name="Description",
            info="Enter the description of the tool.",
            required=True,
        ),
        BoolInput(
            name="return_direct",
            display_name="Return Directly",
            info="Should the tool return the function output directly?",
        ),
        DropdownInput(
            name="tool_function",
            display_name="Tool Function",
            info="Select the function for additional expressions.",
            options=[],
            required=True,
            real_time_refresh=True,
            refresh_button=True,
        ),
        HandleInput(
            name="global_variables",
            display_name="Global Variables",
            info="Enter the global variables or Create Data Component.",
            input_types=["Data"],
            field_type=FieldTypes.DICT,
            is_list=True,
        ),
        MessageTextInput(name="_classes", display_name="Classes", advanced=True),
        MessageTextInput(name="_functions", display_name="Functions", advanced=True),
    ]

    outputs = [
        Output(display_name="Tool", name="result_tool", method="build_tool"),
    ]

    @override
    async def update_build_config(
        self, build_config: dotdict, field_value: Any, field_name: str | None = None
    ) -> dotdict:
        """解析代码并动态更新前端字段。

        关键路径（三步）：
        1) 解析代码提取函数/类
        2) 生成函数参数描述字段
        3) 写回 `_functions`/`_classes` 与选项
        异常流：解析失败时记录状态并回退为错误选项。
        """
        if field_name is None:
            return build_config

        if field_name not in {"tool_code", "tool_function"}:
            return build_config

        try:
            named_functions = {}
            [classes, functions] = self._parse_code(build_config["tool_code"]["value"])
            existing_fields = {}
            if len(build_config) > len(self.DEFAULT_KEYS):
                for key in build_config.copy():
                    if key not in self.DEFAULT_KEYS:
                        existing_fields[key] = build_config.pop(key)

            names = []
            for func in functions:
                named_functions[func["name"]] = func
                names.append(func["name"])

                for arg in func["args"]:
                    field_name = f"{func['name']}|{arg['name']}"
                    if field_name in existing_fields:
                        build_config[field_name] = existing_fields[field_name]
                        continue

                    field = MessageTextInput(
                        display_name=f"{arg['name']}: Description",
                        name=field_name,
                        info=f"Enter the description for {arg['name']}",
                        required=True,
                    )
                    build_config[field_name] = field.to_dict()
            build_config["_functions"]["value"] = json.dumps(named_functions)
            build_config["_classes"]["value"] = json.dumps(classes)
            build_config["tool_function"]["options"] = names
        except Exception as e:  # noqa: BLE001
            self.status = f"Failed to extract names: {e}"
            logger.debug(self.status, exc_info=True)
            build_config["tool_function"]["options"] = ["Failed to parse", str(e)]
        return build_config

    async def build_tool(self) -> Tool:
        """构建结构化工具并绑定用户函数。

        关键路径（三步）：
        1) 解析导入并执行用户代码
        2) 合并全局变量与类定义
        3) 构建参数 schema 并生成工具
        异常流：解析/执行失败会抛出异常并阻断构建。
        """
        local_namespace = {}  # type: ignore[var-annotated]
        modules = self._find_imports(self.tool_code)
        import_code = ""
        for module in modules["imports"]:
            import_code += f"global {module}\nimport {module}\n"
        for from_module in modules["from_imports"]:
            for alias in from_module.names:
                import_code += f"global {alias.name}\n"
            import_code += (
                f"from {from_module.module} import {', '.join([alias.name for alias in from_module.names])}\n"
            )
        # 安全：执行用户代码前先显式导入其依赖。
        exec(import_code, globals())
        # 安全：执行用户代码，仅适用于受控环境。
        exec(self.tool_code, globals(), local_namespace)

        class PythonCodeToolFunc:
            params: dict = {}

            def run(**kwargs):
                for key, arg in kwargs.items():
                    if key not in PythonCodeToolFunc.params:
                        PythonCodeToolFunc.params[key] = arg
                return local_namespace[self.tool_function](**PythonCodeToolFunc.params)

        globals_ = globals()
        local = {}
        local[self.tool_function] = PythonCodeToolFunc
        globals_.update(local)

        if isinstance(self.global_variables, list):
            for data in self.global_variables:
                if isinstance(data, Data):
                    globals_.update(data.data)
        elif isinstance(self.global_variables, dict):
            globals_.update(self.global_variables)

        classes = json.loads(self._attributes["_classes"])
        for class_dict in classes:
            exec("\n".join(class_dict["code"]), globals_)

        named_functions = json.loads(self._attributes["_functions"])
        schema_fields = {}

        for attr in self._attributes:
            if attr in self.DEFAULT_KEYS:
                continue

            func_name = attr.split("|")[0]
            field_name = attr.split("|")[1]
            func_arg = self._find_arg(named_functions, func_name, field_name)
            if func_arg is None:
                msg = f"Failed to find arg: {field_name}"
                raise ValueError(msg)

            field_annotation = func_arg["annotation"]
            field_description = self._get_value(self._attributes[attr], str)

            if field_annotation:
                exec(f"temp_annotation_type = {field_annotation}", globals_)
                schema_annotation = globals_["temp_annotation_type"]
            else:
                schema_annotation = Any
            schema_fields[field_name] = (
                schema_annotation,
                Field(
                    default=func_arg.get("default", Undefined),
                    description=field_description,
                ),
            )

        if "temp_annotation_type" in globals_:
            globals_.pop("temp_annotation_type")

        python_code_tool_schema = None
        if schema_fields:
            python_code_tool_schema = create_model("PythonCodeToolSchema", **schema_fields)

        return StructuredTool.from_function(
            func=local[self.tool_function].run,
            args_schema=python_code_tool_schema,
            name=self.tool_name,
            description=self.tool_description,
            return_direct=self.return_direct,
        )

    async def update_frontend_node(self, new_frontend_node: dict, current_frontend_node: dict):
        """在代码校验后更新前端节点模板。"""
        frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        frontend_node["template"] = await self.update_build_config(
            frontend_node["template"],
            frontend_node["template"]["tool_code"]["value"],
            "tool_code",
        )
        frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        for key in frontend_node["template"]:
            if key in self.DEFAULT_KEYS:
                continue
            frontend_node["template"] = await self.update_build_config(
                frontend_node["template"], frontend_node["template"][key]["value"], key
            )
            frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        return frontend_node

    def _parse_code(self, code: str) -> tuple[list[dict], list[dict]]:
        """解析代码并抽取类与函数信息。

        关键路径（三步）：
        1) 解析 AST 并遍历顶层节点
        2) 提取类/函数代码片段与参数信息
        3) 返回结构化描述列表
        失败语义：多行参数定义直接抛 `ValueError`。
        """
        parsed_code = ast.parse(code)
        lines = code.split("\n")
        classes = []
        functions = []
        for node in parsed_code.body:
            if isinstance(node, ast.ClassDef):
                class_lines = lines[node.lineno - 1 : node.end_lineno]
                class_lines[-1] = class_lines[-1][: node.end_col_offset]
                class_lines[0] = class_lines[0][node.col_offset :]
                classes.append(
                    {
                        "name": node.name,
                        "code": class_lines,
                    }
                )
                continue

            if not isinstance(node, ast.FunctionDef):
                continue

            func = {"name": node.name, "args": []}
                for arg in node.args.args:
                    if arg.lineno != arg.end_lineno:
                        msg = "Multiline arguments are not supported"
                        raise ValueError(msg)

                func_arg = {
                    "name": arg.arg,
                    "annotation": None,
                }

                for default in node.args.defaults:
                    if (
                        arg.lineno > default.lineno
                        or arg.col_offset > default.col_offset
                        or (
                            arg.end_lineno is not None
                            and default.end_lineno is not None
                            and arg.end_lineno < default.end_lineno
                        )
                        or (
                            arg.end_col_offset is not None
                            and default.end_col_offset is not None
                            and arg.end_col_offset < default.end_col_offset
                        )
                    ):
                        continue

                    if isinstance(default, ast.Name):
                        func_arg["default"] = default.id
                    elif isinstance(default, ast.Constant):
                        func_arg["default"] = str(default.value) if default.value is not None else None

                if arg.annotation:
                    annotation_line = lines[arg.annotation.lineno - 1]
                    annotation_line = annotation_line[: arg.annotation.end_col_offset]
                    annotation_line = annotation_line[arg.annotation.col_offset :]
                    func_arg["annotation"] = annotation_line
                    if isinstance(func_arg["annotation"], str) and func_arg["annotation"].count("=") > 0:
                        func_arg["annotation"] = "=".join(func_arg["annotation"].split("=")[:-1]).strip()
                if isinstance(func["args"], list):
                    func["args"].append(func_arg)
            functions.append(func)

        return classes, functions

    def _find_imports(self, code: str) -> dotdict:
        """解析代码并提取 import/from import 语句。"""
        imports: list[str] = []
        from_imports = []
        parsed_code = ast.parse(code)
        for node in parsed_code.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                from_imports.append(node)
        return dotdict({"imports": imports, "from_imports": from_imports})

    def _get_value(self, value: Any, annotation: Any) -> Any:
        """从字段值或字典中读取配置值。"""
        return value if isinstance(value, annotation) else value["value"]

    def _find_arg(self, named_functions: dict, func_name: str, arg_name: str) -> dict | None:
        """在解析结果中查找指定函数参数。"""
        for arg in named_functions[func_name]["args"]:
            if arg["name"] == arg_name:
                return arg
        return None
