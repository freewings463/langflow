"""模块名称：通用工具集合

模块目的：提供跨模块复用的基础工具与格式化逻辑。
主要功能：
- 容器环境探测与 `localhost` 重写
- 从类/方法构建前端模板描述
- 字段类型与显示属性的统一格式化
- Settings 异步更新与辅助工具
使用场景：节点模板生成、配置更新、容器内服务访问等。
关键组件：`detect_container_environment`、`build_template_from_function`、`format_dict`
设计背景：跨模块共享工具集中维护，降低重复实现与耦合。
注意事项：部分函数会原地修改传入字典；容器探测依赖宿主文件与环境变量。
"""

import difflib
import importlib
import inspect
import json
import os
import re
import socket
import struct
from functools import wraps
from pathlib import Path
from typing import Any

from docstring_parser import parse

from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.services.deps import get_settings_service
from lfx.template.frontend_node.constants import FORCE_SHOW_FIELDS
from lfx.utils import constants


def detect_container_environment() -> str | None:
    """检测是否在容器中运行并返回容器类型。

    关键路径：
    1) 检查 `/.dockerenv` 文件
    2) 读取 `/proc/self/cgroup` 关键词
    3) 读取 `container` 环境变量

    异常流：读取系统文件失败时忽略并继续后续策略。
    """
    # `Docker`：`.dockerenv` 文件存在
    if Path("/.dockerenv").exists():
        return "docker"

    # 读取 cgroup 标识
    try:
        with Path("/proc/self/cgroup").open() as f:
            content = f.read()
            if "docker" in content:
                return "docker"
            if "podman" in content:
                return "podman"
    except (FileNotFoundError, PermissionError):
        pass

    # `Podman` 标准环境变量
    if os.getenv("container") == "podman":  # noqa: SIM112
        return "podman"

    return None


def get_container_host() -> str | None:
    """获取容器内访问宿主机的可用主机名或 IP。

    关键路径：
    1) 根据容器类型尝试标准域名
    2) 读取路由表推导网关 IP（Linux 兜底）

    失败语义：无法判断容器或无可用域名/网关时返回 None。
    """
    # 先判断是否在容器环境
    container_type = detect_container_environment()
    if not container_type:
        return None

    # 按容器类型尝试标准域名
    if container_type == "podman":
        # `Podman`：优先 `host.containers.internal`
        try:
            socket.getaddrinfo("host.containers.internal", None)
        except socket.gaierror:
            pass
        else:
            return "host.containers.internal"

        # `Podman Desktop on macOS` 兼容域名
        try:
            socket.getaddrinfo("host.docker.internal", None)
        except socket.gaierror:
            pass
        else:
            return "host.docker.internal"
    else:
        # `Docker`：优先 `host.docker.internal`
        try:
            socket.getaddrinfo("host.docker.internal", None)
        except socket.gaierror:
            pass
        else:
            return "host.docker.internal"

        # 备用域名（少见）
        try:
            socket.getaddrinfo("host.containers.internal", None)
        except socket.gaierror:
            pass
        else:
            return "host.containers.internal"

    # 兜底：读取路由表推导网关 IP（Linux）
    try:
        with Path("/proc/net/route").open() as f:
            # 跳过表头
            next(f)
            for line in f:
                fields = line.strip().split()
                min_field_count = 3  # 最少字段：接口/目的地/网关
                if len(fields) >= min_field_count and fields[1] == "00000000":  # 默认路由
                    # 网关为小端十六进制
                    gateway_hex = fields[2]
                    # 转换为 IPv4
                    gw_int = int(gateway_hex, 16)
                    return socket.inet_ntoa(struct.pack("<L", gw_int))
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        pass

    return None


def transform_localhost_url(url: str | None) -> str | None:
    """将容器内的 `localhost` URL 重写为可访问宿主机的地址。

    关键路径：
    1) 为空则直接返回
    2) 探测容器宿主地址
    3) 替换 `localhost/127.0.0.1`

    失败语义：无法探测宿主地址时返回原始 URL。
    """
    # 空值直接返回，避免 TypeError
    if not url:
        return url

    container_host = get_container_host()

    if not container_host:
        return url

    # 替换本地回环地址
    localhost_patterns = ["localhost", "127.0.0.1"]

    for pattern in localhost_patterns:
        if pattern in url:
            return url.replace(pattern, container_host)

    return url


def unescape_string(s: str):
    """将转义的 `\\n` 替换为真实换行。"""
    return s.replace("\\n", "\n")


def remove_ansi_escape_codes(text):
    """移除字符串中的 ANSI 转义序列。"""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def build_template_from_function(name: str, type_to_loader_dict: dict, *, add_function: bool = False):
    """根据类返回类型构建前端模板描述。

    关键路径：
    1) 校验目标类名存在于 `type_to_loader_dict`
    2) 解析类 docstring 与字段默认值
    3) 组装模板与基类信息

    异常流：类名不存在抛 `ValueError`；默认工厂解析失败记录日志并回退为 None。
    """
    classes = [item.__annotations__["return"].__name__ for item in type_to_loader_dict.values()]

    # 目标类名不存在则直接报错
    if name not in classes:
        msg = f"{name} not found"
        raise ValueError(msg)

    for _type, v in type_to_loader_dict.items():
        if v.__annotations__["return"].__name__ == name:
            class_ = v.__annotations__["return"]

            # 解析类 docstring
            docs = parse(class_.__doc__)

            variables = {"_type": _type}
            for class_field_items, value in class_.model_fields.items():
                if class_field_items == "callback_manager":
                    continue
                variables[class_field_items] = {}
                for name_, value_ in value.__repr_args__():
                    if name_ == "default_factory":
                        try:
                            variables[class_field_items]["default"] = get_default_factory(
                                module=class_.__base__.__module__, function=value_
                            )
                        except Exception:  # noqa: BLE001
                            logger.debug(f"Error getting default factory for {value_}", exc_info=True)
                            variables[class_field_items]["default"] = None
                    elif name_ != "name":
                        variables[class_field_items][name_] = value_

                variables[class_field_items]["placeholder"] = docs.params.get(class_field_items, "")
            # 允许输出为函数类型
            base_classes = get_base_classes(class_)
            if add_function:
                base_classes.append("Callable")

            return {
                "template": format_dict(variables, name),
                "description": docs.short_description or "",
                "base_classes": base_classes,
            }
    return None


def build_template_from_method(
    class_name: str,
    method_name: str,
    type_to_cls_dict: dict,
    *,
    add_function: bool = False,
):
    """根据类方法签名构建模板描述。

    关键路径：
    1) 校验类/方法存在
    2) 解析方法签名与 docstring
    3) 组装模板与基类信息

    失败语义：类名或方法不存在时抛 `ValueError`。
    """
    classes = [item.__name__ for item in type_to_cls_dict.values()]

    # 类名不存在直接报错
    if class_name not in classes:
        msg = f"{class_name} not found."
        raise ValueError(msg)

    for _type, v in type_to_cls_dict.items():
        if v.__name__ == class_name:
            class_ = v

            # 方法不存在直接报错
            if not hasattr(class_, method_name):
                msg = f"Method {method_name} not found in class {class_name}"
                raise ValueError(msg)

            # 获取方法对象
            method = getattr(class_, method_name)

            # 解析方法 docstring
            docs = parse(method.__doc__)

            # 获取方法签名
            sig = inspect.signature(method)

            # 提取参数信息
            params = sig.parameters

            # 构造参数变量映射
            variables = {
                "_type": _type,
                **{
                    name: {
                        "default": (param.default if param.default != param.empty else None),
                        "type": (param.annotation if param.annotation != param.empty else None),
                        "required": param.default == param.empty,
                    }
                    for name, param in params.items()
                    if name not in {"self", "kwargs", "args"}
                },
            }

            base_classes = get_base_classes(class_)

            # 允许输出为函数类型
            if add_function:
                base_classes.append("Callable")

            return {
                "template": format_dict(variables, class_name),
                "description": docs.short_description or "",
                "base_classes": base_classes,
            }
    return None


def get_base_classes(cls):
    """获取类的基类名称集合（用于节点输出类型推断）。"""
    if hasattr(cls, "__bases__") and cls.__bases__:
        bases = cls.__bases__
        result = []
        for base in bases:
            if any(_type in base.__module__ for _type in ["pydantic", "abc"]):
                continue
            result.append(base.__name__)
            base_classes = get_base_classes(base)
            # 去重追加基类名称
            for base_class in base_classes:
                if base_class not in result:
                    result.append(base_class)
    else:
        result = [cls.__name__]
    if not result:
        result = [cls.__name__]
    return list({*result, cls.__name__})


def get_default_factory(module: str, function: str):
    """从 `default_factory` 字符串中解析并调用默认工厂函数。"""
    pattern = r"<function (\w+)>"

    if match := re.search(pattern, function):
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
            )
            warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
            imported_module = importlib.import_module(module)
            return getattr(imported_module, match[1])()
    return None


def update_verbose(d: dict, *, new_value: bool) -> dict:
    """递归更新字典中 `verbose` 字段的值（原地修改）。"""
    for k, v in d.items():
        if isinstance(v, dict):
            update_verbose(v, new_value=new_value)
        elif k == "verbose":
            d[k] = new_value
    return d


def sync_to_async(func):
    """将同步函数包装为异步函数的装饰器。"""

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return async_wrapper


def format_dict(dictionary: dict[str, Any], class_name: str | None = None) -> dict[str, Any]:
    """格式化字段描述字典，补齐类型/显示属性/默认值等信息。

    关键路径：
    1) 解析并规整类型（Optional/List/Mapping/Literal）
    2) 设置 UI 展示/密码/多行标记
    3) 写入特定字段的默认/选项值

    副作用：原地修改 `dictionary` 并返回同一对象。
    """
    for key, value in dictionary.items():
        if key == "_type":
            continue

        type_: str | type = get_type(value)

        if "BaseModel" in str(type_):
            continue

        type_ = remove_optional_wrapper(type_)
        type_ = check_list_type(type_, value)
        type_ = replace_mapping_with_dict(type_)
        type_ = get_type_from_union_literal(type_)

        value["type"] = get_formatted_type(key, type_)
        value["show"] = should_show_field(value, key)
        value["password"] = is_password_field(key)
        value["multiline"] = is_multiline_field(key)

        if key == "dict_":
            set_dict_file_attributes(value)

        replace_default_value_with_actual(value)

        if key == "headers":
            set_headers_value(value)

        add_options_to_field(value, class_name, key)

    return dictionary


# 示例："Union[Literal['f-string'], Literal['jinja2']]" -> "str"
def get_type_from_union_literal(union_literal: str) -> str:
    # 注意：Literal 字面量联合按字符串处理
    if "Literal" in union_literal:
        return "str"
    return union_literal


def get_type(value: Any) -> str | type:
    """从字段字典中提取 `type/annotation`。"""
    # 优先取 `type`，否则取 `annotation`
    type_ = value.get("type") or value.get("annotation")

    return type_ if isinstance(type_, str) else type_.__name__


def remove_optional_wrapper(type_: str | type) -> str:
    """移除类型字符串中的 `Optional[...]` 包装。"""
    if isinstance(type_, type):
        type_ = str(type_)
    if "Optional" in type_:
        type_ = type_.replace("Optional[", "")[:-1]

    return type_


def check_list_type(type_: str, value: dict[str, Any]) -> str:
    """检测是否为集合类型并设置 `list` 标记（原地修改）。"""
    if any(list_type in type_ for list_type in ["List", "Sequence", "Set"]):
        type_ = type_.replace("List[", "").replace("Sequence[", "").replace("Set[", "")[:-1]
        value["list"] = True
    else:
        value["list"] = False

    return type_


def replace_mapping_with_dict(type_: str) -> str:
    """将 `Mapping` 类型名替换为 `dict`。"""
    if "Mapping" in type_:
        type_ = type_.replace("Mapping", "dict")

    return type_


def get_formatted_type(key: str, type_: str) -> str:
    """根据字段名修正展示类型。"""
    if key == "allowed_tools":
        return "Tool"

    if key == "max_value_length":
        return "int"

    return type_


def should_show_field(value: dict[str, Any], key: str) -> bool:
    """判断字段是否应在 UI 中展示。"""
    return (
        (value["required"] and key != "input_variables")
        or key in FORCE_SHOW_FIELDS
        or any(text in key.lower() for text in ["password", "token", "api", "key"])
    )


def is_password_field(key: str) -> bool:
    """判断字段名是否为敏感输入（密码/令牌/密钥）。"""
    return any(text in key.lower() for text in ["password", "token", "api", "key"])


def is_multiline_field(key: str) -> bool:
    """判断字段是否应以多行输入展示。"""
    return key in {
        "suffix",
        "prefix",
        "template",
        "examples",
        "code",
        "headers",
        "format_instructions",
    }


def set_dict_file_attributes(value: dict[str, Any]) -> None:
    """为 `dict_` 字段设置文件上传属性。"""
    value["type"] = "file"
    value["fileTypes"] = [".json", ".yaml", ".yml"]


def replace_default_value_with_actual(value: dict[str, Any]) -> None:
    """将 `default` 替换为 `value`（原地修改）。"""
    if "default" in value:
        value["value"] = value["default"]
        value.pop("default")


def set_headers_value(value: dict[str, Any]) -> None:
    """为 `headers` 字段设置示例默认值。"""
    value["value"] = """{"Authorization": "Bearer <token>"}"""


def add_options_to_field(value: dict[str, Any], class_name: str | None, key: str) -> None:
    """为模型类的 `model_name` 字段添加选项列表。"""
    options_map = {
        "OpenAI": constants.OPENAI_MODELS,
        "ChatOpenAI": constants.CHAT_OPENAI_MODELS,
        "ReasoningOpenAI": constants.REASONING_OPENAI_MODELS,
        "Anthropic": constants.ANTHROPIC_MODELS,
        "ChatAnthropic": constants.ANTHROPIC_MODELS,
    }

    if class_name in options_map and key == "model_name":
        value["options"] = options_map[class_name]
        value["list"] = True
        value["value"] = options_map[class_name][0]


def build_loader_repr_from_data(data: list[Data]) -> str:
    """根据数据列表构建简要展示字符串。"""
    if data:
        avg_length = sum(len(doc.text) for doc in data) / len(data)
        return f"""{len(data)} data
        \nAvg. Data Length (characters): {int(avg_length)}
        Data: {data[:3]}..."""
    return "0 data"


async def update_settings(
    *,
    config: str | None = None,
    cache: str | None = None,
    dev: bool = False,
    remove_api_keys: bool = False,
    components_path: Path | None = None,
    store: bool = True,
    auto_saving: bool = True,
    auto_saving_interval: int = 1000,
    health_check_max_retries: int = 5,
    max_file_size_upload: int = 100,
    webhook_polling_interval: int = 5000,
) -> None:
    """异步更新运行时设置。

    关键路径：
    1) 获取 settings service
    2) 按参数更新配置
    3) 记录关键变更日志

    失败语义：无法获取 settings service 时抛 `RuntimeError`。
    """

    settings_service = get_settings_service()
    if not settings_service:
        msg = "Settings service not found"
        raise RuntimeError(msg)

    if config:
        await logger.adebug(f"Loading settings from {config}")
        await settings_service.settings.update_from_yaml(config, dev=dev)
    if remove_api_keys:
        await logger.adebug(f"Setting remove_api_keys to {remove_api_keys}")
        settings_service.settings.update_settings(remove_api_keys=remove_api_keys)
    if cache:
        await logger.adebug(f"Setting cache to {cache}")
        settings_service.settings.update_settings(cache=cache)
    if components_path:
        await logger.adebug(f"Adding component path {components_path}")
        settings_service.settings.update_settings(components_path=components_path)
    if not store:
        logger.debug("Setting store to False")
        settings_service.settings.update_settings(store=False)
    if not auto_saving:
        logger.debug("Setting auto_saving to False")
        settings_service.settings.update_settings(auto_saving=False)
    if auto_saving_interval is not None:
        logger.debug(f"Setting auto_saving_interval to {auto_saving_interval}")
        settings_service.settings.update_settings(auto_saving_interval=auto_saving_interval)
    if health_check_max_retries is not None:
        logger.debug(f"Setting health_check_max_retries to {health_check_max_retries}")
        settings_service.settings.update_settings(health_check_max_retries=health_check_max_retries)
    if max_file_size_upload is not None:
        logger.debug(f"Setting max_file_size_upload to {max_file_size_upload}")
        settings_service.settings.update_settings(max_file_size_upload=max_file_size_upload)
    if webhook_polling_interval is not None:
        logger.debug(f"Setting webhook_polling_interval to {webhook_polling_interval}")
        settings_service.settings.update_settings(webhook_polling_interval=webhook_polling_interval)


def is_class_method(func, cls):
    """判断函数是否为类方法。"""
    return inspect.ismethod(func) and func.__self__ is cls.__class__


def escape_json_dump(edge_dict):
    """将 JSON 字符串中的双引号替换为 `œ` 用于前端占位。"""
    return json.dumps(edge_dict).replace('"', "œ")


def find_closest_match(string: str, list_of_strings: list[str]) -> str | None:
    """在列表中寻找最相近的字符串（基于 difflib）。"""
    closest_match = difflib.get_close_matches(string, list_of_strings, n=1, cutoff=0.2)
    if closest_match:
        return closest_match[0]
    return None
