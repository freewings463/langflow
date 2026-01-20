"""
模块名称：CLI 脚本加载工具

本模块提供脚本加载与图对象提取能力，主要用于从 Python 脚本中定位并返回 LFX Graph。主要功能包括：
- 动态加载脚本模块并执行
- 校验图实例类型与必要组件
- 从执行结果中提取输出结构

关键组件：
- `load_graph_from_script`：脚本执行入口
- `find_graph_variable`：静态分析定位 `get_graph`/`graph`

设计背景：CLI 需兼容脚本式 flow 入口并提供明确的错误提示。
注意事项：脚本执行会触发用户代码副作用，调用方需在 CLI 层提示风险。
"""

import ast
import importlib.util
import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from lfx.graph import Graph
    from lfx.schema.message import Message


@contextmanager
def temporary_sys_path(path: str):
    """临时将路径加入 `sys.path`。

    契约：进入上下文时插入，退出时移除（若原本不存在）。
    失败语义：无。
    副作用：修改 `sys.path`。
    """
    if path not in sys.path:
        sys.path.insert(0, path)
        try:
            yield
        finally:
            sys.path.remove(path)
    else:
        yield


def _load_module_from_script(script_path: Path) -> Any:
    """从脚本文件加载模块对象。

    契约：返回已执行的模块对象；模块名使用脚本名以便 `inspect` 定位。
    失败语义：无法创建 spec 或执行失败时抛 `ImportError`/原始异常。
    副作用：执行脚本代码并写入 `sys.modules`。
    """
    # 注意：使用脚本名作为模块名，便于 `inspect.getmodule` 定位
    module_name = script_path.stem
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        msg = f"Could not create module spec for '{script_path}'"
        raise ImportError(msg)

    module = importlib.util.module_from_spec(spec)

    # 注意：注册到 `sys.modules`，否则 `inspect.getmodule` 无法识别
    sys.modules[module_name] = module

    try:
        with temporary_sys_path(str(script_path.parent)):
            spec.loader.exec_module(module)
    except Exception:
        if module_name in sys.modules:
            del sys.modules[module_name]
        raise

    return module


def _validate_graph_instance(graph_obj: Any) -> "Graph":
    """校验并返回 Graph 实例。

    契约：要求对象为 `lfx.graph.Graph` 且包含 `Chat Input` 与 `Chat Output` 组件。
    失败语义：类型或组件缺失时抛 `TypeError`/`ValueError`。
    副作用：无。
    """
    from lfx.graph import Graph

    if not isinstance(graph_obj, Graph):
        msg = f"Graph object is not a LFX Graph instance: {type(graph_obj)}"
        raise TypeError(msg)

    display_names: set[str] = set()
    for vertex in graph_obj.vertices:
        if vertex.custom_component is not None:
            display_names.add(vertex.custom_component.display_name)

    if "Chat Input" not in display_names:
        msg = f"Graph does not contain any ChatInput component. Vertices: {display_names}"
        raise ValueError(msg)

    if "Chat Output" not in display_names:
        msg = f"Graph does not contain any ChatOutput component. Vertices: {display_names}"
        raise ValueError(msg)

    return graph_obj


async def load_graph_from_script(script_path: Path) -> "Graph":
    """执行脚本并提取 Graph。

    契约：优先调用 `get_graph()`，否则读取模块变量 `graph`。
    失败语义：执行失败或未找到图时抛 `RuntimeError`。
    副作用：执行用户脚本代码。

    关键路径（三步）：
    1) 动态加载并执行脚本模块
    2) 解析 `get_graph` 或 `graph` 变量
    3) 校验图实例并返回
    """
    try:
        module = _load_module_from_script(script_path)

        graph_obj = None

        if hasattr(module, "get_graph") and callable(module.get_graph):
            get_graph_func = module.get_graph

            if inspect.iscoroutinefunction(get_graph_func):
                graph_obj = await get_graph_func()
            else:
                graph_obj = get_graph_func()

        elif hasattr(module, "graph"):
            graph_obj = module.graph

        if graph_obj is None:
            msg = "No 'graph' variable or 'get_graph()' function found in the executed script"
            raise ValueError(msg)

        return _validate_graph_instance(graph_obj)

    except (
        ImportError,
        AttributeError,
        ModuleNotFoundError,
        SyntaxError,
        TypeError,
        ValueError,
        FileNotFoundError,
    ) as e:
        error_msg = f"Error executing script '{script_path}': {e}"
        raise RuntimeError(error_msg) from e


def extract_message_from_result(results: list) -> str:
    """从结果中提取完整消息体。

    契约：优先从 `Chat Output` 组件读取消息并序列化为 JSON 字符串。
    失败语义：解析失败时返回字符串化的消息或默认提示。
    副作用：无。
    """
    for result in results:
        if (
            hasattr(result, "vertex")
            and result.vertex.custom_component
            and result.vertex.custom_component.display_name == "Chat Output"
        ):
            message: Message = result.result_dict.results["message"]
            try:
                return json.dumps(json.loads(message.model_dump_json()), ensure_ascii=False)
            except (json.JSONDecodeError, AttributeError):
                return str(message)
    return "No response generated"


def extract_text_from_result(results: list) -> str:
    """从结果中提取纯文本内容。

    契约：优先返回 `message.text`；若为 dict 则返回 `text` 字段。
    失败语义：解析失败时返回字符串化内容或默认提示。
    副作用：无。
    """
    for result in results:
        if (
            hasattr(result, "vertex")
            and result.vertex.custom_component
            and result.vertex.custom_component.display_name == "Chat Output"
        ):
            message: dict | Message = result.result_dict.results.get("message")
            try:
                if isinstance(message, dict):
                    text_content = message.get("text") if message.get("text") else str(message)
                else:
                    text_content = message.text
                return str(text_content)
            except AttributeError:
                return str(message)
    return "No response generated"


def extract_structured_result(results: list, *, extract_text: bool = True) -> dict:
    """从结果中抽取结构化输出。

    契约：返回包含 `success/type/component` 等字段的字典。
    失败语义：提取失败时返回 `success=False` 的错误结构。
    副作用：无。

    关键路径（三步）：
    1) 遍历结果查找 `Chat Output` 组件
    2) 根据 `extract_text` 抽取消息或返回原始对象
    3) 组装统一的结构化输出
    """
    for result in results:
        if (
            hasattr(result, "vertex")
            and result.vertex.custom_component
            and result.vertex.custom_component.display_name == "Chat Output"
        ):
            message: Message = result.result_dict.results["message"]
            try:
                result_message = message.text if extract_text and hasattr(message, "text") else message
            except (AttributeError, TypeError, ValueError) as e:
                return {
                    "text": str(message),
                    "type": "message",
                    "component": result.vertex.custom_component.display_name,
                    "component_id": result.vertex.id,
                    "success": True,
                    "warning": f"Could not extract text properly: {e}",
                }

            return {
                "result": result_message,
                "type": "message",
                "component": result.vertex.custom_component.display_name,
                "component_id": result.vertex.id,
                "success": True,
            }
    return {"text": "No response generated", "type": "error", "success": False}


def find_graph_variable(script_path: Path) -> dict | None:
    """静态分析脚本以定位 `get_graph` 或 `graph`。

    契约：优先返回 `get_graph` 定义信息，若不存在则返回 `graph` 赋值信息。
    失败语义：语法或读取错误时返回 None 并输出提示。
    副作用：读取并解析脚本文本。

    关键路径（三步）：
    1) 读取脚本文本并生成 AST
    2) 搜索 `get_graph` 定义（含 async）
    3) 兜底寻找 `graph` 赋值
    """
    try:
        with script_path.open(encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_graph":
                line_number = node.lineno
                is_async = isinstance(node, ast.AsyncFunctionDef)

                return {
                    "line_number": line_number,
                    "type": "function_definition",
                    "function": "get_graph",
                    "is_async": is_async,
                    "arg_count": len(node.args.args),
                    "source_line": content.split("\n")[line_number - 1].strip(),
                }

            if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_graph":
                line_number = node.lineno

                return {
                    "line_number": line_number,
                    "type": "function_definition",
                    "function": "get_graph",
                    "is_async": True,
                    "arg_count": len(node.args.args),
                    "source_line": content.split("\n")[line_number - 1].strip(),
                }

            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "graph":
                        line_number = node.lineno

                        if isinstance(node.value, ast.Call):
                            if isinstance(node.value.func, ast.Name):
                                func_name = node.value.func.id
                            elif isinstance(node.value.func, ast.Attribute):
                                if isinstance(node.value.func.value, ast.Name):
                                    func_name = f"{node.value.func.value.id}.{node.value.func.attr}"
                                else:
                                    func_name = node.value.func.attr
                            else:
                                func_name = "Unknown"

                            arg_count = len(node.value.args) + len(node.value.keywords)

                            return {
                                "line_number": line_number,
                                "type": "function_call",
                                "function": func_name,
                                "arg_count": arg_count,
                                "source_line": content.split("\n")[line_number - 1].strip(),
                            }
                        return {
                            "line_number": line_number,
                            "type": "assignment",
                            "source_line": content.split("\n")[line_number - 1].strip(),
                        }

    except FileNotFoundError:
        typer.echo(f"Error: File '{script_path}' not found.")
        return None
    except SyntaxError as e:
        typer.echo(f"Error: Invalid Python syntax in '{script_path}': {e}")
        return None
    except (OSError, UnicodeDecodeError) as e:
        typer.echo(f"Error parsing '{script_path}': {e}")
        return None
    else:
        return None
