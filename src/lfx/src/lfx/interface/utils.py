"""
模块名称：接口层工具函数

本模块提供接口层常用工具，包括文件加载、图片编码、提示词解析与缓存配置。主要功能包括：
- 从 JSON/YAML 读取配置
- 将图片编码为 `Base64`
- 解析提示词变量并设置 LLM 缓存

关键组件：
- `load_file_into_dict`：配置文件加载
- `try_setting_streaming_options`：流式输出配置
- `set_langchain_cache`：缓存实现注入

设计背景：将通用工具集中管理，供接口层复用。
使用场景：服务启动、组件执行与提示词处理。
注意事项：文件解析失败会抛异常；缓存类型依赖第三方包。
"""

import base64
import json
import os
from io import BytesIO
from pathlib import Path
from string import Formatter

import yaml
from langchain_core.language_models import BaseLanguageModel
from PIL.Image import Image

from lfx.log.logger import logger
from lfx.services.chat.config import ChatConfig
from lfx.services.deps import get_settings_service


def load_file_into_dict(file_path: str) -> dict:
    """读取 JSON/YAML 文件并返回字典。

    契约：`file_path` 必须存在且为 JSON/YAML；返回解析后的 dict。
    副作用：读取磁盘文件。
    关键路径（三步）：1) 校验文件存在 2) JSON 解析失败则回退 YAML 3) 返回结果。
    失败语义：文件不存在抛 `FileNotFoundError`；格式错误抛 `ValueError`。
    决策：优先尝试 JSON，再回退 YAML。
    问题：文件扩展名不可靠（如 UUID 文件名）。
    方案：先解析 JSON，失败后解析 YAML。
    代价：非 JSON/YAML 文件会触发两次解析。
    重评：当文件类型可明确标识时改为按类型解析。
    """
    file_path_ = Path(file_path)
    if not file_path_.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    # 注意：文件名可能为 UUID，无法依赖扩展名判断格式。
    with file_path_.open(encoding="utf-8") as file:
        try:
            data = json.load(file)
        except json.JSONDecodeError:
            file.seek(0)
            data = yaml.safe_load(file)
        except ValueError as exc:
            msg = "Invalid file type. Expected .json or .yaml."
            raise ValueError(msg) from exc
    return data


def pil_to_base64(image: Image) -> str:
    """将 PIL 图片编码为 Base64 字符串。

    契约：输入为 `PIL.Image`；输出为 UTF-8 base64 字符串。
    副作用：在内存中构建 PNG 二进制。
    失败语义：图像编码失败将抛异常。
    决策：固定使用 PNG 编码格式。
    问题：需要稳定、无损的中间表示用于传输。
    方案：统一输出 PNG 以提升兼容性。
    代价：文件体积可能大于 JPEG。
    重评：当需要更高压缩率时支持可选格式。
    """
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue())
    return img_str.decode("utf-8")


def try_setting_streaming_options(langchain_object):
    """尝试在 LangChain 对象上设置流式输出选项。

    契约：当对象包含 LLM 且支持 `streaming`/`stream` 时写入全局配置。
    副作用：修改 LLM 实例的流式属性。
    关键路径（三步）：1) 定位 LLM 2) 判断属性支持 3) 写入配置。
    失败语义：不支持流式属性时静默跳过。
    决策：优先查找 `llm` 或 `llm_chain.llm`。
    问题：不同链式对象封装了不同 LLM 引用路径。
    方案：按常见属性路径探测。
    代价：非常规封装对象可能无法覆盖。
    重评：当新增封装类型时扩展探测路径。
    """
    # 注意：先尝试从对象上获取 `llm`。
    llm = None
    if hasattr(langchain_object, "llm"):
        llm = langchain_object.llm
    elif hasattr(langchain_object, "llm_chain") and hasattr(langchain_object.llm_chain, "llm"):
        llm = langchain_object.llm_chain.llm

    if isinstance(llm, BaseLanguageModel):
        if hasattr(llm, "streaming") and isinstance(llm.streaming, bool):
            llm.streaming = ChatConfig.streaming
        elif hasattr(llm, "stream") and isinstance(llm.stream, bool):
            llm.stream = ChatConfig.streaming

    return langchain_object


def extract_input_variables_from_prompt(prompt: str) -> list[str]:
    """从提示词中提取变量占位符。

    契约：遵循 `str.format` 规则解析并返回去重后的变量名列表。
    副作用：无。
    失败语义：格式解析异常会向上抛出。
    决策：复用 `string.Formatter().parse` 规则。
    问题：需要与 Python 格式化语法保持一致。
    方案：使用标准库解析器而非正则。
    代价：对非标准格式容错较低。
    重评：当支持自定义占位符语法时更换解析策略。
    """
    formatter = Formatter()
    variables: list[str] = []
    seen: set[str] = set()

    # 注意：使用本地绑定减少循环开销。
    variables_append = variables.append
    seen_add = seen.add
    seen_contains = seen.__contains__

    for _, field_name, _, _ in formatter.parse(prompt):
        if field_name and not seen_contains(field_name):
            variables_append(field_name)
            seen_add(field_name)

    return variables


def setup_llm_caching() -> None:
    """根据配置初始化 LLM 缓存。

    契约：读取设置并调用 `set_langchain_cache`。
    副作用：可能修改 LangChain 全局缓存。
    失败语义：导入失败仅记录警告，不抛异常。
    决策：缓存设置失败不阻断启动。
    问题：缓存依赖可能缺失或配置不正确。
    方案：捕获异常并记录日志。
    代价：可能失去缓存带来的性能收益。
    重评：当缓存成为强依赖时改为强失败。
    """
    settings_service = get_settings_service()
    try:
        set_langchain_cache(settings_service.settings)
    except ImportError:
        logger.warning(f"Could not import {settings_service.settings.cache_type}. ")
    except Exception:  # noqa: BLE001
        logger.warning("Could not setup LLM caching.")


def set_langchain_cache(settings) -> None:
    """设置 LangChain 的缓存实现。

    契约：若环境变量或设置指定缓存类型，则初始化并注册缓存实例。
    副作用：修改 LangChain 全局缓存。
    失败语义：缓存类型导入失败仅记录警告。
    决策：优先使用环境变量覆盖设置值。
    问题：运行时需要允许快速切换缓存实现。
    方案：读取 `LANGFLOW_LANGCHAIN_CACHE` 环境变量。
    代价：配置来源增加复杂度。
    重评：当配置中心统一管理时移除环境变量优先级。
    """
    from langchain.globals import set_llm_cache
    from langflow.interface.importing.utils import import_class

    if cache_type := os.getenv("LANGFLOW_LANGCHAIN_CACHE"):
        try:
            cache_class = import_class(f"langchain_community.cache.{cache_type or settings.LANGCHAIN_CACHE}")

            logger.debug(f"Setting up LLM caching with {cache_class.__name__}")
            set_llm_cache(cache_class())
            logger.info(f"LLM caching setup with {cache_class.__name__}")
        except ImportError:
            logger.warning(f"Could not import {cache_type}. ")
    else:
        logger.debug("No LLM cache set.")
