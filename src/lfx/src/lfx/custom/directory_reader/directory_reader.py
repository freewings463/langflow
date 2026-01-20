"""
模块名称：自定义组件目录读取器

本模块提供对自定义组件目录的扫描、校验与菜单构建能力，支持同步与异步两种路径。
主要功能：
- 扫描目录并筛选 Python 文件；
- 校验代码合法性与必要类型提示；
- 构建组件菜单与输出类型列表。

关键组件：
- DirectoryReader：目录扫描与组件信息构建器。
- StringCompressor：可选代码压缩工具。

设计背景：统一自定义组件加载流程，避免各处重复扫描与校验逻辑。
注意事项：路径必须在 `base_path` 限制范围内，且最大深度受 `MAX_DEPTH` 限制。
"""

import ast
import asyncio
import zlib
from pathlib import Path

import anyio
from aiofile import async_open

from lfx.custom.custom_component.component import Component
from lfx.log.logger import logger

MAX_DEPTH = 2


class CustomComponentPathValueError(ValueError):
    """自定义组件路径校验异常。"""
    pass


class StringCompressor:
    def __init__(self, input_string) -> None:
        """初始化字符串压缩器

        契约：接收待压缩字符串并保存到实例。
        """
        self.input_string = input_string

    def compress_string(self):
        """压缩字符串并返回压缩字节

        契约：返回压缩后的 bytes；副作用：写入 `self.compressed_data`。
        关键路径：1) 编码为 bytes 2) zlib 压缩。
        """
        # 实现：字符串先转为 UTF-8 字节。
        byte_data = self.input_string.encode("utf-8")
        # 实现：使用 zlib 压缩。
        self.compressed_data = zlib.compress(byte_data)

        return self.compressed_data

    def decompress_string(self):
        """解压缩并返回原始字符串

        契约：依赖 `self.compressed_data` 已存在；返回解压后的字符串。
        """
        # 实现：解压字节并还原为字符串。
        decompressed_data = zlib.decompress(self.compressed_data)
        # 实现：将字节解码为 UTF-8 字符串。
        return decompressed_data.decode("utf-8")


class DirectoryReader:
    """自定义组件目录读取器

    契约：提供同步/异步扫描与菜单构建；输出结构符合组件菜单格式。
    关键路径：1) 校验路径 2) 读取/校验代码 3) 组装菜单结构。
    决策：限制扫描深度与基础路径
    问题：避免越权读取与过深扫描
    方案：通过 `base_path` 与 `MAX_DEPTH` 限制
    代价：深层目录组件不会被加载
    重评：当需要支持更深层目录结构时
    """

    # 注意：用于限制读取自定义组件的基础路径。
    base_path = ""

    def __init__(self, directory_path, *, compress_code_field=False) -> None:
        """初始化目录读取器

        契约：`directory_path` 为目标目录；`compress_code_field` 决定是否压缩代码字段。
        """
        self.directory_path = directory_path
        self.compress_code_field = compress_code_field

    def get_safe_path(self):
        """返回安全路径或 None

        契约：路径合法时返回原路径，否则返回 None。
        """
        return self.directory_path if self.is_valid_path() else None

    def is_valid_path(self) -> bool:
        """校验路径是否在允许范围内

        契约：当 `base_path` 为空时允许所有路径，否则要求相对路径关系成立。
        """
        fullpath = Path(self.directory_path).resolve()
        return not self.base_path or fullpath.is_relative_to(self.base_path)

    def is_empty_file(self, file_content):
        """判断文件内容是否为空白。"""
        return len(file_content.strip()) == 0

    def filter_loaded_components(self, data: dict, *, with_errors: bool) -> dict:
        """按是否有错误筛选组件列表

        契约：返回仅包含符合条件的菜单结构。
        关键路径：1) 构建组件对象 2) 按错误标记过滤 3) 生成菜单列表。
        异常流：构建失败时记录日志并跳过该组件。
        排障入口：日志 `Skipping component ... (load error)`。
        """
        from lfx.custom.utils import build_component

        items = []
        for menu in data["menu"]:
            components = []
            for component in menu["components"]:
                try:
                    if component["error"] if with_errors else not component["error"]:
                        component_tuple = (*build_component(component), component)
                        components.append(component_tuple)
                except Exception as exc:  # noqa: BLE001  # 注意：组件构建失败需隔离处理。
                    logger.debug(
                        f"Skipping component {component['name']} from {component['file']} (load error)",
                        exc_info=exc,
                    )
                    continue
            items.append({"name": menu["name"], "path": menu["path"], "components": components})
        filtered = [menu for menu in items if menu["components"]]
        logger.debug(f"Filtered components {'with errors' if with_errors else ''}: {len(filtered)}")
        return {"menu": filtered}

    def validate_code(self, file_content) -> bool:
        """校验 Python 代码语法

        契约：语法合法返回 True，否则返回 False。
        """
        try:
            ast.parse(file_content)
        except SyntaxError:
            return False
        return True

    def validate_build(self, file_content):
        """检查是否存在 build 函数定义。"""
        return "def build" in file_content

    def read_file_content(self, file_path):
        """读取文件内容（同步）

        契约：读取成功返回字符串；文件不存在返回 None。
        异常流：编码错误时自动降级为二进制读取并按 UTF-8 解码。
        """
        file_path_ = Path(file_path)
        if not file_path_.is_file():
            return None
        try:
            with file_path_.open(encoding="utf-8") as file:
                # 注意：部分系统默认编码可能不兼容 UTF-8。
                return file.read()
        except UnicodeDecodeError:
            # 注意：Windows 上常见编码问题，改用二进制读取再 UTF-8 解码。
            with file_path_.open("rb") as f:
                return f.read().decode("utf-8")

    async def aread_file_content(self, file_path):
        """读取文件内容（异步）

        契约：读取成功返回字符串；文件不存在返回 None。
        异常流：编码错误时降级为二进制读取并按 UTF-8 解码。
        """
        file_path_ = anyio.Path(file_path)
        if not await file_path_.is_file():
            return None
        try:
            async with async_open(str(file_path_), encoding="utf-8") as file:
                # 注意：部分系统默认编码可能不兼容 UTF-8。
                return await file.read()
        except UnicodeDecodeError:
            # 注意：Windows 上常见编码问题，改用二进制读取再 UTF-8 解码。
            async with async_open(str(file_path_), "rb") as f:
                return (await f.read()).decode("utf-8")

    def get_files(self):
        """扫描目录并返回 .py 文件列表

        契约：返回满足路径与深度限制的 Python 文件路径列表。
        关键路径：1) 校验安全路径 2) 递归扫描 3) 过滤无效文件。
        异常流：路径不安全时抛 `CustomComponentPathValueError`。
        决策：限制扫描深度为 `MAX_DEPTH`
        问题：深层扫描易引发性能与越权风险
        方案：只允许 <= MAX_DEPTH 的文件
        代价：更深层组件不会被加载
        重评：当目录结构需求变化时
        """
        if not (safe_path := self.get_safe_path()):
            msg = f"The path needs to start with '{self.base_path}'."
            raise CustomComponentPathValueError(msg)

        file_list = []
        safe_path_obj = Path(safe_path)
        for file_path in safe_path_obj.rglob("*.py"):
            # 注意：跳过 `deactivated` 目录下的文件。
            if "deactivated" in file_path.parent.name:
                continue

            # 实现：计算相对深度以限制扫描范围。
            relative_depth = len(file_path.relative_to(safe_path_obj).parts)

            # 注意：仅包含指定深度内且非 __ 前缀的文件。
            if relative_depth <= MAX_DEPTH and file_path.is_file() and not file_path.name.startswith("__"):
                file_list.append(str(file_path))
        return file_list

    def find_menu(self, response, menu_name):
        """在菜单列表中查找指定菜单。"""
        return next(
            (menu for menu in response["menu"] if menu["name"] == menu_name),
            None,
        )

    def _is_type_hint_imported(self, type_hint_name: str, code: str) -> bool:
        """判断类型提示是否从 typing 导入

        契约：返回是否存在 `from typing import <type_hint_name>`。
        """
        module = ast.parse(code)

        return any(
            isinstance(node, ast.ImportFrom)
            and node.module == "typing"
            and any(alias.name == type_hint_name for alias in node.names)
            for node in ast.walk(module)
        )

    def _is_type_hint_used_in_args(self, type_hint_name: str, code: str) -> bool:
        """判断类型提示是否在函数参数中使用

        契约：代码合法时返回使用与否；语法错误返回 False。
        """
        try:
            module = ast.parse(code)

            for node in ast.walk(module):
                if isinstance(node, ast.FunctionDef):
                    for arg in node.args.args:
                        if self._is_type_hint_in_arg_annotation(arg.annotation, type_hint_name):
                            return True
        except SyntaxError:
            # 注意：语法错误时直接视为未使用。
            return False
        return False

    def _is_type_hint_in_arg_annotation(self, annotation, type_hint_name: str) -> bool:
        """辅助判断注解中是否包含指定类型提示。"""
        return (
            annotation is not None
            and isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == type_hint_name
        )

    def is_type_hint_used_but_not_imported(self, type_hint_name: str, code: str) -> bool:
        """检查类型提示是否使用但未导入

        契约：若使用且未导入返回 True；语法错误时返回 True。
        决策：语法错误时返回 True
        问题：语法错误会导致误判或遗漏
        方案：在错误场景下保守判定为问题
        代价：可能误报
        重评：当引入更稳健语法解析时
        """
        try:
            return self._is_type_hint_used_in_args(type_hint_name, code) and not self._is_type_hint_imported(
                type_hint_name, code
            )
        except SyntaxError:
            # 注意：语法错误保守返回 True。
            return True

    def process_file(self, file_path):
        """处理单个文件并返回校验结果

        契约：返回 `(bool, content_or_error)`；成功为 True 且 content 为代码文本。
        关键路径：1) 读取文件 2) 校验空/语法/类型提示 3) 可选压缩代码。
        异常流：读取异常返回 False + 错误信息。
        决策：`Optional` 未导入视为错误
        问题：类型提示不完整会导致运行时解析失败
        方案：检测使用与导入一致性
        代价：可能拒绝部分可运行代码
        重评：当类型检查策略调整时
        """
        try:
            file_content = self.read_file_content(file_path)
        except Exception:  # noqa: BLE001  # 注意：读取失败需兜底为错误信息。
            logger.exception(f"Error while reading file {file_path}")
            return False, f"Could not read {file_path}"

        if file_content is None:
            return False, f"Could not read {file_path}"
        if self.is_empty_file(file_content):
            return False, "Empty file"
        if not self.validate_code(file_content):
            return False, "Syntax error"
        if self._is_type_hint_used_in_args("Optional", file_content) and not self._is_type_hint_imported(
            "Optional", file_content
        ):
            return (
                False,
                "Type hint 'Optional' is used but not imported in the code.",
            )
        if self.compress_code_field:
            file_content = str(StringCompressor(file_content).compress_string())
        return True, file_content

    def build_component_menu_list(self, file_paths):
        """构建组件菜单列表（同步）

        契约：返回 `{"menu": [...]}` 结构。
        关键路径：1) 逐文件处理 2) 解析输出类型 3) 生成菜单结构。
        异常流：单文件失败记录错误并继续构建。
        排障入口：日志 `Error while processing file`。
        """
        response = {"menu": []}
        logger.debug("-------------------- Building component menu list --------------------")

        for file_path in file_paths:
            file_path_ = Path(file_path)
            menu_name = file_path_.parent.name
            filename = file_path_.name
            validation_result, result_content = self.process_file(file_path)
            if not validation_result:
                logger.error(f"Error while processing file {file_path}")

            menu_result = self.find_menu(response, menu_name) or {
                "name": menu_name,
                "path": str(file_path_.parent),
                "components": [],
            }
            component_name = filename.split(".")[0]
            # 注意：UI 展示使用 CamelCase 名称。
            if "_" in component_name:
                component_name_camelcase = " ".join(word.title() for word in component_name.split("_"))
            else:
                component_name_camelcase = component_name

            if validation_result:
                try:
                    output_types = self.get_output_types_from_code(result_content)
                except Exception:  # noqa: BLE001  # 注意：输出类型解析失败需降级。
                    logger.debug("Error while getting output types from code", exc_info=True)
                    output_types = [component_name_camelcase]
            else:
                output_types = [component_name_camelcase]

            component_info = {
                "name": component_name_camelcase,
                "output_types": output_types,
                "file": filename,
                "code": result_content if validation_result else "",
                "error": "" if validation_result else result_content,
            }
            menu_result["components"].append(component_info)

            if menu_result not in response["menu"]:
                response["menu"].append(menu_result)
        logger.debug("-------------------- Component menu list built --------------------")
        return response

    async def process_file_async(self, file_path):
        """异步处理单个文件并返回校验结果

        契约：返回 `(bool, content_or_error)`；失败返回错误信息。
        关键路径：1) 异步读取 2) 校验空/语法/类型提示 3) 可选压缩。
        """
        try:
            file_content = await self.aread_file_content(file_path)
        except Exception:  # noqa: BLE001  # 注意：读取失败需兜底为错误信息。
            await logger.aexception(f"Error while reading file {file_path}")
            return False, f"Could not read {file_path}"

        if file_content is None:
            return False, f"Could not read {file_path}"
        if self.is_empty_file(file_content):
            return False, "Empty file"
        if not self.validate_code(file_content):
            return False, "Syntax error"
        if self._is_type_hint_used_in_args("Optional", file_content) and not self._is_type_hint_imported(
            "Optional", file_content
        ):
            return (
                False,
                "Type hint 'Optional' is used but not imported in the code.",
            )
        if self.compress_code_field:
            file_content = str(StringCompressor(file_content).compress_string())
        return True, file_content

    async def abuild_component_menu_list(self, file_paths):
        """构建组件菜单列表（异步）

        契约：返回 `{"menu": [...]}` 结构。
        关键路径：1) 并发处理文件 2) 解析输出类型 3) 生成菜单结构。
        异常流：单文件失败记录日志并继续。
        """
        response = {"menu": []}
        await logger.adebug("-------------------- Async Building component menu list --------------------")

        tasks = [self.process_file_async(file_path) for file_path in file_paths]
        results = await asyncio.gather(*tasks)

        for file_path, (validation_result, result_content) in zip(file_paths, results, strict=True):
            file_path_ = Path(file_path)
            menu_name = file_path_.parent.name
            filename = file_path_.name

            if not validation_result:
                await logger.aerror(f"Error while processing file {file_path}")

            menu_result = self.find_menu(response, menu_name) or {
                "name": menu_name,
                "path": str(file_path_.parent),
                "components": [],
            }
            component_name = filename.split(".")[0]

            if "_" in component_name:
                component_name_camelcase = " ".join(word.title() for word in component_name.split("_"))
            else:
                component_name_camelcase = component_name

            if validation_result:
                try:
                    output_types = await asyncio.to_thread(self.get_output_types_from_code, result_content)
                except Exception:  # noqa: BLE001  # 注意：输出类型解析失败需降级。
                    await logger.aexception("Error while getting output types from code")
                    output_types = [component_name_camelcase]
            else:
                output_types = [component_name_camelcase]

            component_info = {
                "name": component_name_camelcase,
                "output_types": output_types,
                "file": filename,
                "code": result_content if validation_result else "",
                "error": "" if validation_result else result_content,
            }
            menu_result["components"].append(component_info)

            if menu_result not in response["menu"]:
                response["menu"].append(menu_result)

        await logger.adebug("-------------------- Component menu list built --------------------")
        return response

    @staticmethod
    def get_output_types_from_code(code: str) -> list:
        """从代码中解析输出类型

        契约：返回类型名列表；仅包含具备 `__name__` 的类型。
        """
        custom_component = Component(_code=code)
        types_list = custom_component._get_function_entrypoint_return_type

        # 实现：提取类型名称。
        return [type_.__name__ for type_ in types_list if hasattr(type_, "__name__")]
