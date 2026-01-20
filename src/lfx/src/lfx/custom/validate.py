"""
模块名称：自定义组件代码校验与动态构造

本模块负责校验自定义组件代码、动态执行函数与类构造，并处理必要的导入与上下文准备。
主要功能：
- 语法解析与导入校验；
- 动态执行函数/类并构建可调用对象；
- 兼容 Langflow/lfx 命名空间差异。

设计背景：自定义组件需在运行时加载，必须在安全边界内完成解析与构造。
注意事项：动态 exec 存在风险，需确保代码来源可信并经过上层校验。
"""

import ast
import contextlib
import importlib
import warnings
from types import FunctionType
from typing import Optional, Union

from langchain_core._api.deprecation import LangChainDeprecationWarning
from pydantic import ValidationError

from lfx.field_typing.constants import CUSTOM_COMPONENT_SUPPORTED_TYPES, DEFAULT_IMPORT_STRING
from lfx.log.logger import logger

_LANGFLOW_IS_INSTALLED = False

with contextlib.suppress(ImportError):
    import langflow  # noqa: F401

    _LANGFLOW_IS_INSTALLED = True


def add_type_ignores() -> None:
    """为低版本 Python 补充 TypeIgnore AST 节点。

    契约：若 ast.TypeIgnore 不存在则注入占位类型。
    """
    if not hasattr(ast, "TypeIgnore"):

        class TypeIgnore(ast.AST):
            _fields = ()

        ast.TypeIgnore = TypeIgnore  # type: ignore[assignment, misc]


def validate_code(code):
    """校验代码的导入与函数可执行性

    契约：返回包含 import 与 function 错误列表的字典。
    关键路径：1) AST 解析 2) 校验 import 可用性 3) 在 Langflow 上下文中执行函数体。
    异常流：解析失败返回错误信息，不抛异常。
    """
    # 注意：errors 结构用于前端展示。
    errors = {"imports": {"errors": []}, "function": {"errors": []}}

    # 实现：解析 AST 以校验语法与导入。
    try:
        tree = ast.parse(code)
    except Exception as e:  # noqa: BLE001
        if hasattr(logger, "opt"):
            logger.debug("Error parsing code", exc_info=True)
        else:
            logger.debug("Error parsing code")
        errors["function"]["errors"].append(str(e))
        return errors

    # 注意：补齐 type_ignores 以兼容不同版本 AST。
    add_type_ignores()
    tree.type_ignores = []

    # 实现：校验 import 模块是否可导入。
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    importlib.import_module(alias.name)
                except ModuleNotFoundError as e:
                    errors["imports"]["errors"].append(str(e))

    # 实现：在 Langflow 常用上下文中执行函数以发现运行期错误。
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            code_obj = compile(ast.Module(body=[node], type_ignores=[]), "<string>", "exec")
            try:
                # 实现：创建包含常用类型的执行上下文。
                exec_globals = _create_langflow_execution_context()
                exec(code_obj, exec_globals)
            except Exception as e:  # noqa: BLE001
                logger.debug("Error executing function code", exc_info=True)
                errors["function"]["errors"].append(str(e))

    # 注意：返回错误字典供上层处理。
    return errors


def _create_langflow_execution_context():
    """构建带常用类型的执行上下文

    契约：返回包含 Data/Message/DataFrame/Component 等类型的字典。
    决策：缺失类型时创建空壳类型
    问题：组件代码可能引用类型但运行环境未安装
    方案：以空壳类型占位避免 NameError
    代价：类型行为不可用，仅用于校验
    重评：当运行时强依赖完整环境时
    """
    context = {}

    # 注意：为模板常用类型提供兜底。
    try:
        from lfx.schema.dataframe import DataFrame

        context["DataFrame"] = DataFrame
    except ImportError:
        # 注意：缺失时使用空壳类型占位。
        context["DataFrame"] = type("DataFrame", (), {})

    try:
        from lfx.schema.message import Message

        context["Message"] = Message
    except ImportError:
        context["Message"] = type("Message", (), {})

    try:
        from lfx.schema.data import Data

        context["Data"] = Data
    except ImportError:
        context["Data"] = type("Data", (), {})

    try:
        from lfx.custom import Component

        context["Component"] = Component
    except ImportError:
        context["Component"] = type("Component", (), {})

    try:
        from lfx.io import HandleInput, Output, TabInput

        context["HandleInput"] = HandleInput
        context["Output"] = Output
        context["TabInput"] = TabInput
    except ImportError:
        context["HandleInput"] = type("HandleInput", (), {})
        context["Output"] = type("Output", (), {})
        context["TabInput"] = type("TabInput", (), {})

    # 注意：补齐常用 typing 名称。
    try:
        from typing import Any, Optional, Union

        context["Any"] = Any
        context["Dict"] = dict
        context["List"] = list
        context["Optional"] = Optional
        context["Union"] = Union
    except ImportError:
        pass

    return context


def eval_function(function_string: str):
    """从字符串构造函数对象

    契约：返回函数对象；若未找到函数抛 `ValueError`。
    关键路径：1) exec 代码 2) 在命名空间中查找函数。
    """
    # 注意：使用独立命名空间避免污染全局。
    namespace: dict = {}

    # 实现：执行代码并从命名空间中提取函数。
    exec(function_string, namespace)
    function_object = next(
        (
            obj
            for name, obj in namespace.items()
            if isinstance(obj, FunctionType) and obj.__code__.co_filename == "<string>"
        ),
        None,
    )
    if function_object is None:
        msg = "Function string does not contain a function"
        raise ValueError(msg)
    return function_object


def execute_function(code, function_name, *args, **kwargs):
    """执行指定函数并返回结果

    契约：解析代码并执行指定函数；找不到模块时报错。
    关键路径：1) 注入 import 2) 提取函数 AST 3) 编译执行。
    异常流：缺少模块抛 `ModuleNotFoundError`，执行失败抛 `ValueError`。
    """
    add_type_ignores()

    module = ast.parse(code)
    exec_globals = globals().copy()

    for node in module.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    exec(
                        f"{alias.asname or alias.name} = importlib.import_module('{alias.name}')",
                        exec_globals,
                        locals(),
                    )
                    exec_globals[alias.asname or alias.name] = importlib.import_module(alias.name)
                except ModuleNotFoundError as e:
                    msg = f"Module {alias.name} not found. Please install it and try again."
                    raise ModuleNotFoundError(msg) from e

    function_code = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    function_code.parent = None
    code_obj = compile(ast.Module(body=[function_code], type_ignores=[]), "<string>", "exec")
    exec_locals = dict(locals())
    try:
        exec(code_obj, exec_globals, exec_locals)
    except Exception as exc:
        msg = "Function string does not contain a function"
        raise ValueError(msg) from exc

    # 注意：将函数绑定到 exec_globals 以便调用。
    exec_globals[function_name] = exec_locals[function_name]

    return exec_globals[function_name](*args, **kwargs)


def create_function(code, function_name):
    """创建可调用的函数包装器

    契约：返回包装函数；内部负责导入依赖并执行目标函数。
    关键路径：1) 解析 AST 2) 导入依赖 3) 编译函数 4) 返回包装器。
    """
    if not hasattr(ast, "TypeIgnore"):

        class TypeIgnore(ast.AST):
            _fields = ()

        ast.TypeIgnore = TypeIgnore

    module = ast.parse(code)
    exec_globals = globals().copy()

    for node in module.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                try:
                    if isinstance(node, ast.ImportFrom):
                        module_name = node.module
                        exec_globals[alias.asname or alias.name] = getattr(
                            importlib.import_module(module_name), alias.name
                        )
                    else:
                        module_name = alias.name
                        exec_globals[alias.asname or alias.name] = importlib.import_module(module_name)
                except ModuleNotFoundError as e:
                    msg = f"Module {alias.name} not found. Please install it and try again."
                    raise ModuleNotFoundError(msg) from e

    function_code = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    function_code.parent = None
    code_obj = compile(ast.Module(body=[function_code], type_ignores=[]), "<string>", "exec")
    exec_locals = dict(locals())
    with contextlib.suppress(Exception):
        exec(code_obj, exec_globals, exec_locals)
    exec_globals[function_name] = exec_locals[function_name]

    # 注意：执行时注入必要模块，确保函数依赖可用。
    def wrapped_function(*args, **kwargs):
        for module_name, module in exec_globals.items():
            if isinstance(module, type(importlib)):
                globals()[module_name] = module

        return exec_globals[function_name](*args, **kwargs)

    return wrapped_function


def create_class(code, class_name):
    """动态创建类并返回类对象

    契约：返回目标类对象；语法或校验失败抛 `ValueError`。
    关键路径：1) 解析代码 2) 准备作用域 3) 编译并构造类。
    """
    # 注意：补齐 TypeIgnore 以兼容低版本 AST。
    if not hasattr(ast, "TypeIgnore"):
        ast.TypeIgnore = create_type_ignore_class()

    # 注意：兼容历史导入路径，统一指向 langflow.custom。
    code = code.replace("from langflow import CustomComponent", "from langflow.custom import CustomComponent")
    code = code.replace(
        "from langflow.interface.custom.custom_component import CustomComponent",
        "from langflow.custom import CustomComponent",
    )

    code = DEFAULT_IMPORT_STRING + "\n" + code
    try:
        module = ast.parse(code)
        exec_globals = prepare_global_scope(module)

        class_code = extract_class_code(module, class_name)
        compiled_class = compile_class_code(class_code)

        return build_class_constructor(compiled_class, exec_globals, class_name)

    except SyntaxError as e:
        msg = f"Syntax error in code: {e!s}"
        raise ValueError(msg) from e
    except NameError as e:
        msg = f"Name error (possibly undefined variable): {e!s}"
        raise ValueError(msg) from e
    except ValidationError as e:
        messages = [error["msg"].split(",", 1) for error in e.errors()]
        error_message = "\n".join([message[1] if len(message) > 1 else message[0] for message in messages])
        raise ValueError(error_message) from e
    except Exception as e:
        msg = f"Error creating class. {type(e).__name__}({e!s})."
        raise ValueError(msg) from e


def create_type_ignore_class():
    """创建 TypeIgnore AST 节点占位类。"""

    class TypeIgnore(ast.AST):
        _fields = ()

    return TypeIgnore


def _import_module_with_warnings(module_name):
    """导入模块并按需抑制弃用警告。"""
    if "langchain" in module_name:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LangChainDeprecationWarning)
            return importlib.import_module(module_name)
    else:
        return importlib.import_module(module_name)


def _handle_module_attributes(imported_module, node, module_name, exec_globals):
    """处理 `from x import y` 的属性导入。"""
    for alias in node.names:
        try:
            # 注意：先尝试作为属性获取。
            exec_globals[alias.name] = getattr(imported_module, alias.name)
        except AttributeError:
            # 注意：失败则尝试导入完整模块路径。
            full_module_path = f"{module_name}.{alias.name}"
            exec_globals[alias.name] = importlib.import_module(full_module_path)


def prepare_global_scope(module):
    """准备执行全局作用域

    契约：返回包含已导入模块的全局作用域字典。
    异常流：缺失模块时抛 `ModuleNotFoundError`。
    """
    exec_globals = globals().copy()
    imports = []
    import_froms = []
    definitions = []

    # 实现：收集 import、from import 与定义节点。
    for node in module.body:
        if isinstance(node, ast.Import):
            imports.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            import_froms.append(node)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.Assign):
            definitions.append(node)

    for node in imports:
        for alias in node.names:
            module_name = alias.name
            # 注意：完整导入路径以确保子模块可用。
            module_obj = importlib.import_module(module_name)

            # 注意：确定绑定到的变量名。
            if alias.asname:
                # 注意：别名导入直接绑定到别名变量。
                variable_name = alias.asname
                exec_globals[variable_name] = module_obj
            else:
                # 注意：点号导入绑定到顶层包名。
                variable_name = module_name.split(".")[0]
                exec_globals[variable_name] = importlib.import_module(variable_name)

    for node in import_froms:
        module_names_to_try = [node.module]

        # 注意：兼容 langflow -> lfx 的模块迁移路径。
        if node.module and node.module.startswith("langflow."):
            lfx_module_name = node.module.replace("langflow.", "lfx.", 1)
            module_names_to_try.append(lfx_module_name)

        success = False
        last_error = None

        for module_name in module_names_to_try:
            try:
                imported_module = _import_module_with_warnings(module_name)
                _handle_module_attributes(imported_module, node, module_name, exec_globals)

                success = True
                break

            except ModuleNotFoundError as e:
                last_error = e
                continue

        if not success:
            # 注意：保留真实缺失模块信息以便排障。
            if last_error:
                raise last_error
            msg = f"Module {node.module} not found. Please install it and try again"
            raise ModuleNotFoundError(msg)

    if definitions:
        combined_module = ast.Module(body=definitions, type_ignores=[])
        compiled_code = compile(combined_module, "<string>", "exec")
        exec(compiled_code, exec_globals)

    return exec_globals


def extract_class_code(module, class_name):
    """从 AST 中提取指定类定义

    契约：返回目标类的 AST 节点。
    """
    class_code = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name)

    class_code.parent = None
    return class_code


def compile_class_code(class_code):
    """编译类 AST 为可执行代码对象。"""
    return compile(ast.Module(body=[class_code], type_ignores=[]), "<string>", "exec")


def build_class_constructor(compiled_class, exec_globals, class_name):
    """构建动态类的构造器

    契约：返回可创建目标类的构造函数。
    """
    exec_locals = dict(locals())
    exec(compiled_class, exec_globals, exec_locals)
    exec_globals[class_name] = exec_locals[class_name]

    # 注意：执行时将导入模块注入全局，保证类依赖可用。
    def build_custom_class():
        for module_name, module in exec_globals.items():
            if isinstance(module, type(importlib)):
                globals()[module_name] = module

        return exec_globals[class_name]

    return build_custom_class()


# TODO: 移除此函数
def get_default_imports(code_string):
    """返回动态类构造所需的默认导入。"""
    default_imports = {
        "Optional": Optional,
        "List": list,
        "Dict": dict,
        "Union": Union,
    }
    langflow_imports = list(CUSTOM_COMPONENT_SUPPORTED_TYPES.keys())
    necessary_imports = find_names_in_code(code_string, langflow_imports)
    langflow_module = importlib.import_module("lfx.field_typing")
    default_imports.update({name: getattr(langflow_module, name) for name in necessary_imports})

    return default_imports


def find_names_in_code(code, names):
    """在代码中查找指定名称集合

    契约：返回在代码中出现的名称集合。
    """
    return {name for name in names if name in code}


def extract_function_name(code):
    """提取代码中第一个函数名。"""
    module = ast.parse(code)
    for node in module.body:
        if isinstance(node, ast.FunctionDef):
            return node.name
    msg = "No function definition found in the code string"
    raise ValueError(msg)


def extract_class_name(code: str) -> str:
    """提取代码中第一个 Component 子类名

    契约：返回类名；找不到时抛 `TypeError`。
    """
    try:
        module = ast.parse(code)
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue

            # 注意：仅检查基类名包含 Component/LC，后续可增强判定。
            for base in node.bases:
                if isinstance(base, ast.Name) and any(pattern in base.id for pattern in ["Component", "LC"]):
                    return node.name

        msg = f"No Component subclass found in the code string. Code snippet: {code[:100]}"
        raise TypeError(msg)
    except SyntaxError as e:
        msg = f"Invalid Python code: {e!s}"
        raise ValueError(msg) from e
