"""
模块名称：自定义组件代码解析器

模块目的：解析自定义组件源码并输出结构化信息（imports/functions/classes/global_vars）。
使用场景：前端表单生成、编辑器提示与调试展示需要函数签名与类信息。
主要功能包括：
- 解析 AST 并抽取导入、函数、类与全局变量
- 在受控环境中解析返回类型与继承链
- 提供解析结果缓存以降低重复开销

关键组件：
- CodeParser：解析入口与聚合器
- CodeSyntaxError：语法错误包装
- find_class_ast_node：基类源码定位

设计背景：需要尽量避免执行用户代码，但部分类型解析仍依赖 `eval`。
注意：`eval_custom_component_code` 会执行用户代码，存在副作用与安全风险。
"""

import ast
import contextlib
import inspect
import traceback
from itertools import starmap
from pathlib import Path
from typing import Any

from cachetools import TTLCache, keys
from fastapi import HTTPException

from lfx.custom.eval import eval_custom_component_code
from lfx.custom.schema import CallableCodeDetails, ClassCodeDetails, MissingDefault
from lfx.log.logger import logger


class CodeSyntaxError(HTTPException):
    """语法错误包装异常。

    契约：用于将 `SyntaxError` 转为 FastAPI 可返回的 `HTTPException`。
    失败语义：继承 `HTTPException` 的 `status_code`/`detail` 行为。
    决策：复用 FastAPI 异常体系。
    问题：上游需要统一 HTTP 层错误返回。
    方案：继承 `HTTPException` 并直接抛出。
    代价：解析层与 Web 层耦合。
    重评：当解析器需要独立为无 Web 依赖库时。
    """
    pass


def get_data_type():
    """返回 `Data` 类型以延迟导入。

    契约：无参，返回 `lfx.schema.data.Data` 类对象。
    副作用：触发模块导入。
    失败语义：导入失败会抛 `ImportError`。
    决策：延迟导入 `Data`。
    问题：避免模块加载时引入循环依赖。
    方案：在函数内导入并返回类型。
    代价：首次调用有导入开销。
    重评：当依赖稳定且无循环时。
    """
    from lfx.schema.data import Data

    return Data


def find_class_ast_node(class_obj):
    """定位类在源码中的 AST 节点。

    契约：输入类对象，输出 `(class_node, import_nodes)`。
    副作用：读取类定义文件并解析 AST。
    关键路径（三步）：1) 定位源码文件；2) 解析 AST；3) 收集目标类与 import。
    失败语义：无源码文件时返回 `(None, [])`。
    性能：AST 全量遍历，耗时与文件大小线性相关。
    排障：检查 `inspect.getsourcefile` 结果与源码文件可读性。
    决策：使用 AST 遍历而非正则。
    问题：需要稳定定位类定义与 import。
    方案：`ast.walk` 遍历节点。
    代价：全量遍历，随文件大小增长。
    重评：当需要增量解析或缓存 AST 时。
    """
    source_file = inspect.getsourcefile(class_obj)
    if not source_file:
        return None, []

    source_code = Path(source_file).read_text(encoding="utf-8")

    tree = ast.parse(source_code)

    class_node = None
    import_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_obj.__name__:
            class_node = node
        elif isinstance(node, ast.Import | ast.ImportFrom):
            import_nodes.append(node)

    return class_node, import_nodes


def imports_key(*args, **kwargs):
    """生成包含 `imports` 的缓存键。

    契约：输入任意 `*args/**kwargs`，要求 `kwargs` 含 `imports`。
    失败语义：缺失 `imports` 会触发 `KeyError`。
    决策：将 `imports` 纳入缓存键。
    问题：返回类型解析依赖导入列表。
    方案：基于 `keys.methodkey` 并拼接 imports。
    代价：键长度随 imports 增长。
    重评：当采用更细粒度缓存策略时。
    """
    imports = kwargs.pop("imports")
    key = keys.methodkey(*args, **kwargs)
    key += tuple(imports)
    return key


class CodeParser:
    """Python 源码解析器，用于抽取结构化信息。

    契约：输入源码或类对象，输出包含 imports/functions/classes/global_vars 的字典。
    关键路径：`parse_code` 驱动 AST 遍历并聚合结果。
    决策：基于标准库 `ast` 解析而非直接执行代码。
    问题：需要安全获取结构信息。
    方案：解析 AST，并在限定场景下 `eval` 返回类型。
    代价：类型解析依赖 `exec/eval`，存在安全与副作用风险。
    重评：当引入纯静态类型解析器时。
    """

    def __init__(self, code: str | type) -> None:
        """初始化解析器并规范化输入源码。

        契约：输入 `code`（字符串或类），建立解析状态与缓存。
        副作用：当 `code` 为类时读取源代码；创建 TTLCache(1024, 60s)。
        关键路径（三步）：1) 校验并规范化 `code`；2) 初始化 `data` 容器；3) 建立 handler 映射。
        异常流：非类 `type` 抛 `ValueError`；`inspect.getsource` 可能抛 `OSError`。
        性能：初始化成本与源码长度线性相关。
        排障：优先检查异常消息与源码可读取性。
        决策：支持 `str` 与 `type` 两种输入。
        问题：调用方可能只持有类对象而非源码。
        方案：若为类则使用 `inspect.getsource` 获取源码。
        代价：依赖源码可用性，可能触发 `OSError`。
        重评：当需要支持文件路径或 AST 输入时。
        """
        self.cache: TTLCache = TTLCache(maxsize=1024, ttl=60)
        if isinstance(code, type):
            if not inspect.isclass(code):
                msg = "The provided code must be a class."
                raise ValueError(msg)
            code = inspect.getsource(code)
        self.code = code
        self.data: dict[str, Any] = {
            "imports": [],
            "functions": [],
            "classes": [],
            "global_vars": [],
        }
        self.handlers = {
            ast.Import: self.parse_imports,
            ast.ImportFrom: self.parse_imports,
            ast.FunctionDef: self.parse_functions,
            ast.ClassDef: self.parse_classes,
            ast.Assign: self.parse_global_vars,
        }

    def get_tree(self):
        """解析源码并返回 AST。

        契约：无参，返回 `ast.AST`。
        关键路径（三步）：1) 调用 `ast.parse`；2) 捕获 `SyntaxError`；3) 转换为 HTTP 异常。
        异常流：语法错误抛 `CodeSyntaxError`，包含 `traceback`。
        性能：解析复杂源码时耗时与源码长度线性相关。
        排障：检查 `detail.error` 与 `traceback` 以定位语法行。
        决策：将语法错误映射为 400。
        问题：前端需要可读错误与定位信息。
        方案：封装 `SyntaxError` 为 `CodeSyntaxError`。
        代价：解析层与 HTTP 状态码绑定。
        重评：当解析器脱离 Web 环境时。
        """
        try:
            tree = ast.parse(self.code)
        except SyntaxError as err:
            raise CodeSyntaxError(
                status_code=400,
                detail={"error": err.msg, "traceback": traceback.format_exc()},
            ) from err

        return tree

    def parse_node(self, node: ast.stmt | ast.AST) -> None:
        """按节点类型分派解析函数。

        契约：输入 `ast.AST`，若存在 handler 则更新 `self.data`。
        关键路径：读取 `handlers` 映射并调用目标解析器。
        决策：使用类型到函数的映射表。
        问题：避免长链 `isinstance` 判断。
        方案：在初始化时构建 `handlers`。
        代价：仅支持显式注册的节点类型。
        重评：当需要动态扩展更多节点时。
        """
        if handler := self.handlers.get(type(node)):
            handler(node)  # type: ignore[operator]

    def parse_imports(self, node: ast.Import | ast.ImportFrom) -> None:
        """抽取 import 语句并写入 `self.data["imports"]`。

        契约：支持 `ast.Import` 与 `ast.ImportFrom`；`ImportFrom` 保存 `(module, name)` 元组。
        关键路径：遍历 alias 并保留别名信息。
        决策：保存为字符串或二元组混合结构。
        问题：需要保留 `from x import y as z` 语义。
        方案：对 `ImportFrom` 记录 `(module, name)`。
        代价：后续解析需识别两种结构。
        重评：当需要统一结构或引入数据类时。
        """
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self.data["imports"].append(f"{alias.name} as {alias.asname}")
                else:
                    self.data["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    self.data["imports"].append((node.module, f"{alias.name} as {alias.asname}"))
                else:
                    self.data["imports"].append((node.module, alias.name))

    def parse_functions(self, node: ast.FunctionDef) -> None:
        """登记函数定义并保存结构化详情。

        契约：输入 `ast.FunctionDef`，追加到 `self.data["functions"]`。
        关键路径：调用 `parse_callable_details` 生成详情。
        决策：统一用 `CallableCodeDetails` 表达函数。
        问题：前端需要统一函数字段结构。
        方案：封装到 `CallableCodeDetails`。
        代价：类型解析可能失败而为空。
        重评：当需要额外字段（装饰器等）时。
        """
        self.data["functions"].append(self.parse_callable_details(node))

    def parse_arg(self, arg, default):
        """解析单个参数及默认值。

        契约：返回包含 `name`/`default`/可选 `type` 的字典。
        失败语义：注解无法 `ast.unparse` 时抛异常。
        决策：使用 `ast.unparse` 生成类型字符串。
        问题：需要将注解转为可展示文本。
        方案：直接反解析 AST 注解。
        代价：复杂注解可能生成长字符串。
        重评：当需要更结构化的注解表示时。
        """
        arg_dict = {"name": arg.arg, "default": default}
        if arg.annotation:
            arg_dict["type"] = ast.unparse(arg.annotation)
        return arg_dict

    def construct_eval_env(self, return_type_str: str, imports) -> dict:
        """构建返回类型解析所需的 `eval` 环境。

        契约：输入返回类型字符串与 imports，返回可用于 `eval` 的环境字典。
        副作用：执行 `exec` 导入匹配模块。
        关键路径（三步）：1) 遍历 imports；2) 匹配类型字符串；3) `exec` 导入。
        异常流：导入失败会抛 `ImportError` 或 `ModuleNotFoundError`。
        性能：遍历 imports 的成本与数量线性相关。
        排障：定位导入失败的模块名与别名匹配逻辑。
        注意：`exec` 仅执行 import 语句，但仍存在执行风险。
        决策：按返回类型字符串选择性导入。
        问题：完整导入全部模块成本高且风险大。
        方案：仅导入命中类型名的模块/别名。
        代价：类型字符串不匹配时解析为 `None`。
        重评：当改用静态类型解析器时。
        """
        eval_env: dict = {}
        for import_entry in imports:
            if isinstance(import_entry, tuple):
                module, name = import_entry
                if name in return_type_str:
                    exec(f"import {module}", eval_env)
                    exec(f"from {module} import {name}", eval_env)
            else:
                module = import_entry
                alias = None
                if " as " in module:
                    module, alias = module.split(" as ")
                if module in return_type_str or (alias and alias in return_type_str):
                    exec(f"import {module} as {alias or module}", eval_env)
        return eval_env

    def parse_callable_details(self, node: ast.FunctionDef) -> dict[str, Any]:
        """抽取函数/方法的结构化详情。

        契约：输入 `ast.FunctionDef`，输出 `CallableCodeDetails` 的字典。
        关键路径（三步）：1) 解析返回类型；2) 解析参数/正文；3) 汇总字段。
        异常流：返回类型解析失败时保持 `return_type=None`，其余异常向外传播。
        性能：`ast.unparse` 与参数解析耗时与函数体规模相关。
        排障：检查返回类型字符串与 imports 是否匹配。
        决策：在可行时对返回类型执行 `eval`。
        问题：需要真实类型对象以供前端展示。
        方案：使用 `construct_eval_env` + `eval`。
        代价：类型解析受导入可见性影响。
        重评：当类型解析改为纯字符串模式时。
        """
        return_type = None
        if node.returns:
            return_type_str = ast.unparse(node.returns)
            eval_env = self.construct_eval_env(return_type_str, tuple(self.data["imports"]))

            with contextlib.suppress(NameError):
                return_type = eval(return_type_str, eval_env)  # noqa: S307

        func = CallableCodeDetails(
            name=node.name,
            doc=ast.get_docstring(node),
            args=self.parse_function_args(node),
            body=self.parse_function_body(node),
            return_type=return_type,
            has_return=self.parse_return_statement(node),
        )

        return func.model_dump()

    def parse_function_args(self, node: ast.FunctionDef) -> list[dict[str, Any]]:
        """汇总函数参数清单。

        契约：输出按调用顺序组合的参数列表，包含位置参数、`*args`、仅关键字、`**kwargs`。
        关键路径：按四类参数依次拼接。
        决策：保留 `**kwargs` 以完整呈现签名。
        问题：前端需要完整参数结构。
        方案：分别解析四类参数后合并。
        代价：部分前端可能需过滤 `**kwargs`。
        重评：当 UI 明确不展示 `**kwargs` 时。
        """
        args = []

        args += self.parse_positional_args(node)
        args += self.parse_varargs(node)
        args += self.parse_keyword_args(node)
        args += self.parse_kwargs(node)

        return args

    def parse_positional_args(self, node: ast.FunctionDef) -> list[dict[str, Any]]:
        """解析位置参数及默认值。

        契约：返回与 `node.args.args` 对齐的参数列表。
        关键路径（三步）：1) 计算缺失默认值数；2) 反解析默认值；3) 拼接 defaults。
        注意：字符串 "None" 被归一为 `None`。
        异常流：`ast.unparse` 失败会抛异常并中断解析。
        性能：默认值反解析耗时与参数数量线性相关。
        排障：检查默认值表达式是否可被 `ast.unparse` 解析。
        决策：用 `MissingDefault` 标记缺省值。
        问题：需要区分“无默认值”和“默认值为 None”。
        方案：缺省用 `MissingDefault()`，`"None"` 归一为 `None`。
        代价：调用方必须识别 `MissingDefault` 类型。
        重评：当改用显式 `has_default` 标志时。
        """
        num_args = len(node.args.args)
        num_defaults = len(node.args.defaults)
        num_missing_defaults = num_args - num_defaults
        missing_defaults = [MissingDefault()] * num_missing_defaults
        default_values = [ast.unparse(default).strip("'") if default else None for default in node.args.defaults]
        default_values = [None if value == "None" else value for value in default_values]

        defaults = missing_defaults + default_values

        return list(starmap(self.parse_arg, zip(node.args.args, defaults, strict=True)))

    def parse_varargs(self, node: ast.FunctionDef) -> list[dict[str, Any]]:
        """解析 `*args` 参数。

        契约：当存在 `vararg` 时返回单元素列表，否则返回空列表。
        决策：保持与其他解析函数一致的列表返回结构。
        问题：上游期望统一的参数集合结构。
        方案：无论有无 `*args` 均返回 list。
        代价：调用方需处理空列表。
        重评：当改用字典聚合参数时。
        """
        args = []

        if node.args.vararg:
            args.append(self.parse_arg(node.args.vararg, None))

        return args

    def parse_keyword_args(self, node: ast.FunctionDef) -> list[dict[str, Any]]:
        """解析仅关键字参数。

        契约：返回 `kwonlyargs` 对应的参数列表，默认值可为 `None`。
        关键路径：对齐 `kw_defaults` 并填充缺失值。
        决策：使用 `None` 表示缺失默认值。
        问题：`kw_defaults` 可能短于 `kwonlyargs`。
        方案：前置补齐 `None` 保持索引对齐。
        代价：无法区分“无默认值”与“显式默认 None”。
        重评：当需要 `MissingDefault` 区分时。
        """
        kw_defaults = [None] * (len(node.args.kwonlyargs) - len(node.args.kw_defaults)) + [
            ast.unparse(default) if default else None for default in node.args.kw_defaults
        ]

        return list(starmap(self.parse_arg, zip(node.args.kwonlyargs, kw_defaults, strict=True)))

    def parse_kwargs(self, node: ast.FunctionDef) -> list[dict[str, Any]]:
        """解析 `**kwargs` 参数。

        契约：存在 `kwarg` 时返回单元素列表，否则返回空列表。
        决策：与 `parse_varargs` 同一返回策略。
        问题：统一参数解析接口。
        方案：始终返回列表。
        代价：调用方需处理空列表。
        重评：当 `**kwargs` 不再需要展示时。
        """
        args = []

        if node.args.kwarg:
            args.append(self.parse_arg(node.args.kwarg, None))

        return args

    def parse_function_body(self, node: ast.FunctionDef) -> list[str]:
        """抽取函数体源码字符串。

        契约：返回 `node.body` 每行的 `ast.unparse` 结果列表。
        失败语义：`ast.unparse` 失败会抛异常。
        决策：保留源码字符串而非 AST 节点。
        问题：前端展示需要可读代码片段。
        方案：直接反解析为字符串。
        代价：丢失原始格式/注释。
        重评：当需要保留格式化或注释时。
        """
        return [ast.unparse(line) for line in node.body]

    def parse_return_statement(self, node: ast.FunctionDef) -> bool:
        """判断函数是否包含 return（含嵌套分支）。

        契约：返回 `bool`，表示任意路径存在 `return`。
        关键路径（三步）：1) 递归遍历语句；2) 覆盖 if/try/loop/with；3) 汇总结果。
        异常流：不支持的节点类型视为无 return，不抛异常。
        性能：递归深度与语句嵌套层数相关。
        排障：若结果异常，检查是否存在未覆盖的语句类型。
        决策：递归遍历而非控制流图构建。
        问题：需要轻量判断函数是否有返回值。
        方案：覆盖常见语句节点的递归检测。
        代价：不保证覆盖所有复杂控制流。
        重评：当需要精确控制流分析时。
        """

        def has_return(node):
            if isinstance(node, ast.Return):
                return True
            if isinstance(node, ast.If):
                return any(has_return(child) for child in node.body) or any(has_return(child) for child in node.orelse)
            if isinstance(node, ast.Try):
                return (
                    any(has_return(child) for child in node.body)
                    or any(has_return(child) for child in node.handlers)
                    or any(has_return(child) for child in node.finalbody)
                )
            if isinstance(node, ast.For | ast.While):
                return any(has_return(child) for child in node.body) or any(has_return(child) for child in node.orelse)
            if isinstance(node, ast.With):
                return any(has_return(child) for child in node.body)
            return False

        return any(has_return(child) for child in node.body)

    def parse_assign(self, stmt):
        """解析 `Assign` 并提取名称和值。

        契约：仅支持 `ast.Name` 目标，返回 `{name, value}` 或 `None`。
        决策：忽略复杂赋值目标（如解构）。
        问题：前端只需要简单类属性展示。
        方案：只处理 `ast.Name`。
        代价：解构赋值不会被记录。
        重评：当需要支持解构或多目标时。
        """
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                return {"name": target.id, "value": ast.unparse(stmt.value)}
        return None

    def parse_ann_assign(self, stmt):
        """解析 `AnnAssign` 并提取名称、值与注解。

        契约：仅支持 `ast.Name` 目标，返回 `{name, value, annotation}` 或 `None`。
        决策：只处理简单变量标注。
        问题：类属性注解常为单名目标。
        方案：过滤非 `ast.Name` 目标。
        代价：复杂目标注解会被忽略。
        重评：当需要解析属性解构时。
        """
        if isinstance(stmt.target, ast.Name):
            return {
                "name": stmt.target.id,
                "value": ast.unparse(stmt.value) if stmt.value else None,
                "annotation": ast.unparse(stmt.annotation),
            }
        return None

    def parse_function_def(self, stmt):
        """解析类方法并标记是否为 `__init__`。

        契约：返回 `(method_dict, is_init)` 元组。
        决策：将 `__init__` 与普通方法拆分存储。
        问题：前端需要将构造函数单独展示。
        方案：检测 `stmt.name == "__init__"`。
        代价：对别名构造函数无感知。
        重评：当需要支持 `__post_init__` 等特例时。
        """
        method = self.parse_callable_details(stmt)
        return (method, True) if stmt.name == "__init__" else (method, False)

    def get_base_classes(self):
        """获取自定义组件的基类列表。

        契约：返回基类集合（含两级继承）。
        副作用：执行用户代码以构造组件类。
        失败语义：执行失败会抛异常并记录空结果。
        决策：执行代码以获得真实继承链。
        问题：纯 AST 无法解析动态继承。
        方案：`eval_custom_component_code` 实例化并遍历 `__bases__`。
        代价：存在执行副作用与安全风险。
        重评：当引入安全沙箱或静态继承解析时。
        """
        try:
            bases = self.execute_and_inspect_classes(self.code)
        except Exception:
            bases = []
            raise
        return bases

    def parse_classes(self, node: ast.ClassDef) -> None:
        """抽取类定义与继承信息。

        契约：输入 `ast.ClassDef`，追加 `ClassCodeDetails` 到 `self.data["classes"]`。
        关键路径（三步）：1) 获取基类并定位 AST；2) 采集属性/方法；3) 合并并写入结果。
        异常流：基类源码不可用时跳过；解析异常会记录日志并继续。
        性能：基类数量越多，AST 解析与遍历成本越高。
        排障：查看日志关键字 `Error finding base class node`。
        注意：过滤 `CustomComponent/Component/BaseComponent` 以避免噪音。
        决策：合并基类 AST 以补全继承信息。
        问题：前端需要显示继承属性与方法。
        方案：解析基类源码并合并节点。
        代价：基类源码不可用时信息不完整。
        重评：当仅展示当前类信息即可时。
        """
        bases = self.get_base_classes()
        nodes = []
        for base in bases:
            if base.__name__ == node.name or base.__name__ in {"CustomComponent", "Component", "BaseComponent"}:
                continue
            try:
                class_node, import_nodes = find_class_ast_node(base)
                if class_node is None:
                    continue
                for import_node in import_nodes:
                    self.parse_imports(import_node)
                nodes.append(class_node)
            except Exception:  # noqa: BLE001
                logger.exception("Error finding base class node")
        nodes.insert(0, node)
        class_details = ClassCodeDetails(
            name=node.name,
            doc=ast.get_docstring(node),
            bases=[b.__name__ for b in bases],
            attributes=[],
            methods=[],
            init=None,
        )
        for _node in nodes:
            self.process_class_node(_node, class_details)
        self.data["classes"].append(class_details.model_dump())

    def process_class_node(self, node, class_details) -> None:
        """遍历类节点并填充属性/方法列表。

        契约：更新 `class_details.attributes/methods/init`。
        决策：将 `AnnAssign` 与 `Assign` 统一为 attributes。
        问题：需要兼容带注解与不带注解的属性。
        方案：分别解析两类赋值节点并写入同一列表。
        代价：属性顺序依赖 AST 顺序。
        重评：当需要按类别分组时。
        """
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                if attr := self.parse_assign(stmt):
                    class_details.attributes.append(attr)
            elif isinstance(stmt, ast.AnnAssign):
                if attr := self.parse_ann_assign(stmt):
                    class_details.attributes.append(attr)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                method, is_init = self.parse_function_def(stmt)
                if is_init:
                    class_details.init = method
                else:
                    class_details.methods.append(method)

    def parse_global_vars(self, node: ast.Assign) -> None:
        """抽取模块级变量。

        契约：记录目标名称与赋值表达式字符串。
        失败语义：非 `ast.Name` 目标将记录 `ast.dump` 字符串。
        决策：保留原始表达式字符串以便展示。
        问题：前端需要展示定义表达式。
        方案：`ast.unparse` 直接生成字符串。
        代价：表达式可能较长且无格式化。
        重评：当需要结构化表达式时。
        """
        global_var = {
            "targets": [t.id if hasattr(t, "id") else ast.dump(t) for t in node.targets],
            "value": ast.unparse(node.value),
        }
        self.data["global_vars"].append(global_var)

    def execute_and_inspect_classes(self, code: str):
        """执行代码并返回两级继承基类。

        契约：输入源码字符串，返回基类列表。
        副作用：执行用户代码并实例化组件。
        异常流：执行或实例化异常向外传播。
        性能：执行成本取决于用户代码复杂度。
        排障：查看用户代码运行时异常与初始化堆栈。
        决策：通过实例化获取真实 `__bases__`。
        问题：继承关系可能由运行时生成。
        方案：执行并检查 `__bases__` 两级。
        代价：执行风险与性能开销。
        重评：当引入安全沙箱或缓存继承信息时。
        """
        custom_component_class = eval_custom_component_code(code)
        custom_component = custom_component_class(_code=code)
        dunder_class = custom_component.__class__
        bases = []
        for base in dunder_class.__bases__:
            bases.append(base)
            bases.extend(base.__bases__)
        return bases

    def parse_code(self) -> dict[str, Any]:
        """运行完整解析流程并返回结构化结果。

        契约：返回包含 imports/functions/classes/global_vars 的字典。
        关键路径（三步）：1) 构建 AST；2) 遍历节点；3) 聚合并返回结果。
        异常流：语法错误会抛 `CodeSyntaxError`。
        性能：`ast.walk` 遍历节点数量与源码规模线性相关。
        排障：先检查语法错误，再检查 `self.data` 是否被覆盖。
        决策：使用 `ast.walk` 一次遍历收集信息。
        问题：需要在单次遍历中收集多类型节点。
        方案：基于 handler 分派收集。
        代价：遍历顺序非源码顺序。
        重评：当需要保持源码顺序时。
        """
        tree = self.get_tree()

        for node in ast.walk(tree):
            self.parse_node(node)
        return self.data
