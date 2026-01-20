"""
模块名称：统一模型元数据与选项构建

本模块聚合各提供方模型元数据，并提供统一的模型选项构建、API key 解析与
模型实例化逻辑，主要用于 UI 选择与运行时模型构建。
主要功能包括：
- 聚合各提供方模型元数据并提供过滤查询
- 根据用户配置构建可用模型/嵌入模型选项
- 校验提供方 API key 并构建模型实例

关键组件：
- `get_unified_models_detailed`
- `get_language_model_options` / `get_embedding_model_options`
- `get_llm` / `update_model_options_in_build_config`

设计背景：将分散的模型元数据与运行时构建逻辑集中管理，减少跨模块重复。
注意事项：本模块会访问数据库变量与外部 SDK，异常需在上层处理。
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Callable

import contextlib

from lfx.base.models.anthropic_constants import ANTHROPIC_MODELS_DETAILED
from lfx.base.models.google_generative_ai_constants import (
    GOOGLE_GENERATIVE_AI_MODELS_DETAILED,
)
from lfx.base.models.ollama_constants import OLLAMA_EMBEDDING_MODELS_DETAILED, OLLAMA_MODELS_DETAILED
from lfx.base.models.openai_constants import OPENAI_EMBEDDING_MODELS_DETAILED, OPENAI_MODELS_DETAILED
from lfx.base.models.watsonx_constants import WATSONX_MODELS_DETAILED
from lfx.log.logger import logger
from lfx.services.deps import get_variable_service, session_scope
from lfx.utils.async_helpers import run_until_complete


@lru_cache(maxsize=1)
def get_model_classes():
    """延迟加载模型类，避免模块级导入可选依赖。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_ibm import ChatWatsonx
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    from lfx.base.models.google_generative_ai_model import ChatGoogleGenerativeAIFixed

    return {
        "ChatOpenAI": ChatOpenAI,
        "ChatAnthropic": ChatAnthropic,
        "ChatGoogleGenerativeAIFixed": ChatGoogleGenerativeAIFixed,
        "ChatOllama": ChatOllama,
        "ChatWatsonx": ChatWatsonx,
    }


@lru_cache(maxsize=1)
def get_embedding_classes():
    """延迟加载嵌入模型类，避免模块级导入可选依赖。"""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_ibm import WatsonxEmbeddings
    from langchain_ollama import OllamaEmbeddings
    from langchain_openai import OpenAIEmbeddings

    return {
        "GoogleGenerativeAIEmbeddings": GoogleGenerativeAIEmbeddings,
        "OpenAIEmbeddings": OpenAIEmbeddings,
        "OllamaEmbeddings": OllamaEmbeddings,
        "WatsonxEmbeddings": WatsonxEmbeddings,
    }


@lru_cache(maxsize=1)
def get_model_provider_metadata():
    """返回模型提供方的图标与变量名映射。"""
    return {
        "OpenAI": {
            "icon": "OpenAI",
            "variable_name": "OPENAI_API_KEY",
            "api_docs_url": "https://platform.openai.com/docs/overview",
        },
        "Anthropic": {
            "icon": "Anthropic",
            "variable_name": "ANTHROPIC_API_KEY",
            "api_docs_url": "https://console.anthropic.com/docs",
        },
        "Google Generative AI": {
            "icon": "GoogleGenerativeAI",
            "variable_name": "GOOGLE_API_KEY",
            "api_docs_url": "https://aistudio.google.com/app/apikey",
        },
        "Ollama": {
            "icon": "Ollama",
            "variable_name": "OLLAMA_BASE_URL",
            "api_docs_url": "https://ollama.com/",
        },
        "IBM WatsonX": {
            "icon": "IBM",
            "variable_name": "WATSONX_APIKEY",
            "api_docs_url": "https://www.ibm.com/products/watsonx",
        },
    }


model_provider_metadata = get_model_provider_metadata()


@lru_cache(maxsize=1)
def get_models_detailed():
    """汇总所有提供方的模型元数据列表。"""
    return [
        ANTHROPIC_MODELS_DETAILED,
        OPENAI_MODELS_DETAILED,
        OPENAI_EMBEDDING_MODELS_DETAILED,
        GOOGLE_GENERATIVE_AI_MODELS_DETAILED,
        OLLAMA_MODELS_DETAILED,
        OLLAMA_EMBEDDING_MODELS_DETAILED,
        WATSONX_MODELS_DETAILED,
    ]


MODELS_DETAILED = get_models_detailed()


@lru_cache(maxsize=1)
def get_model_provider_variable_mapping() -> dict[str, str]:
    """返回提供方名称到环境变量名的映射。"""
    return {provider: meta["variable_name"] for provider, meta in model_provider_metadata.items()}


def get_model_providers() -> list[str]:
    """返回去重且排序后的提供方名称列表。"""
    return sorted({md.get("provider", "Unknown") for group in MODELS_DETAILED for md in group})


def get_unified_models_detailed(
    providers: list[str] | None = None,
    model_name: str | None = None,
    model_type: str | None = None,
    *,
    include_unsupported: bool | None = None,
    include_deprecated: bool | None = None,
    only_defaults: bool = False,
    **metadata_filters,
):
    """返回统一模型元数据列表，并支持多维过滤。

    契约：返回按提供方聚合的模型清单，包含 `provider`/`models`/`num_models` 等字段。
    关键路径（三步）：
    1) 汇总所有 `_MODELS_DETAILED` 列表
    2) 按参数与元数据过滤模型
    3) 按提供方分组并标记默认模型
    异常流：该函数不主动捕获异常，依赖上游确保元数据结构正确。
    性能瓶颈：主要为线性过滤与分组。
    排障入口：检查 `include_unsupported`/`include_deprecated` 与过滤参数是否一致。

    说明：
    - `providers` 指定提供方白名单；
    - `model_name` 精确匹配模型名；
    - `model_type` 匹配元数据字段；
    - `only_defaults=True` 时仅返回默认模型（每个提供方前 5 个标记为默认）。
    """
    if include_unsupported is None:
        include_unsupported = False
    if include_deprecated is None:
        include_deprecated = False

    # 汇总所有 *_MODELS_DETAILED 列表
    all_models: list[dict] = []
    for models_detailed in MODELS_DETAILED:
        all_models.extend(models_detailed)

    # 应用过滤规则
    filtered_models: list[dict] = []
    for md in all_models:
        # 非显式包含时跳过不支持模型
        if (not include_unsupported) and md.get("not_supported", False):
            continue

        # 非显式包含时跳过弃用模型
        if (not include_deprecated) and md.get("deprecated", False):
            continue

        if providers and md.get("provider") not in providers:
            continue
        if model_name and md.get("name") != model_name:
            continue
        if model_type and md.get("model_type") != model_type:
            continue
        # 任意元数据键值精确匹配
        if any(md.get(k) != v for k, v in metadata_filters.items()):
            continue

        filtered_models.append(md)

    # 按提供方分组
    provider_map: dict[str, list[dict]] = {}
    for metadata in filtered_models:
        prov = metadata.get("provider", "Unknown")
        provider_map.setdefault(prov, []).append(
            {
                "model_name": metadata.get("name"),
                "metadata": {k: v for k, v in metadata.items() if k not in ("provider", "name")},
            }
        )

    # 标记每个提供方的前 5 个模型为默认（按列表顺序）
    # 并可选仅保留默认模型
    default_model_count = 5  # 每个提供方的默认数量

    for prov, models in provider_map.items():
        for i, model in enumerate(models):
            if i < default_model_count:
                model["metadata"]["default"] = True
            else:
                model["metadata"]["default"] = False

        # 若仅需要默认模型则过滤
        if only_defaults:
            provider_map[prov] = [m for m in models if m["metadata"].get("default", False)]

    # 组装返回结构
    return [
        {
            "provider": prov,
            "models": models,
            "num_models": len(models),
            **model_provider_metadata.get(prov, {}),
        }
        for prov, models in provider_map.items()
    ]


def get_api_key_for_provider(user_id: UUID | str | None, provider: str, api_key: str | None = None) -> str | None:
    """从用户输入或全局变量中获取 API key。

    契约：优先使用显式传入的 `api_key`；否则按用户变量查找。
    失败语义：查不到返回 `None`。
    """
    # 优先使用显式传入的 API key
    if api_key:
        return api_key

    # 无用户信息时无法读取全局变量
    if user_id is None or (isinstance(user_id, str) and user_id == "None"):
        return None

    # 提供方到变量名映射
    provider_variable_map = {
        "OpenAI": "OPENAI_API_KEY",
        "Anthropic": "ANTHROPIC_API_KEY",
        "Google Generative AI": "GOOGLE_API_KEY",
        "IBM WatsonX": "WATSONX_APIKEY",
    }

    variable_name = provider_variable_map.get(provider)
    if not variable_name:
        return None

    # 从全局变量中读取
    async def _get_variable():
        async with session_scope() as session:
            variable_service = get_variable_service()
            if variable_service is None:
                return None
            return await variable_service.get_variable(
                user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
                name=variable_name,
                field="",
                session=session,
            )

    return run_until_complete(_get_variable())


def validate_model_provider_key(variable_name: str, api_key: str) -> None:
    """通过最小调用校验提供方 API key。

    契约：校验失败抛 `ValueError`；其余异常默认视为网络问题并忽略。
    注意：此校验不覆盖所有错误场景，仅用于快速发现无效密钥。
    """
    # 变量名到提供方映射
    provider_map = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
        "GOOGLE_API_KEY": "Google Generative AI",
        "WATSONX_APIKEY": "IBM WatsonX",
        "OLLAMA_BASE_URL": "Ollama",
    }

    provider = provider_map.get(variable_name)
    if not provider:
        return  # 非可校验的提供方变量

    # 获取该提供方的第一个可用模型
    try:
        models = get_unified_models_detailed(providers=[provider])
        if not models or not models[0].get("models"):
            return  # 无可用模型，跳过校验

        first_model = models[0]["models"][0]["model_name"]
    except Exception:  # noqa: BLE001
        return  # 无法获取模型，跳过校验

    # 按提供方执行最小化测试
    try:
        if provider == "OpenAI":
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(api_key=api_key, model_name=first_model, max_tokens=1)
            llm.invoke("test")
        elif provider == "Anthropic":
            from langchain_anthropic import ChatAnthropic

            llm = ChatAnthropic(anthropic_api_key=api_key, model=first_model, max_tokens=1)
            llm.invoke("test")
        elif provider == "Google Generative AI":
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(google_api_key=api_key, model=first_model, max_tokens=1)
            llm.invoke("test")
        elif provider == "IBM WatsonX":
            from langchain_ibm import ChatWatsonx

            default_url = "https://us-south.ml.cloud.ibm.com"
            llm = ChatWatsonx(
                apikey=api_key,
                url=default_url,
                model_id=first_model,
                project_id="dummy_project_for_validation",  # 校验用的虚拟 project_id
                params={"max_new_tokens": 1},
            )
            llm.invoke("test")

        elif provider == "Ollama":
            # Ollama 为本地地址，仅校验可访问性
            import requests

            response = requests.get(f"{api_key}/api/tags", timeout=5)
            if response.status_code != requests.codes.ok:
                msg = "Invalid Ollama base URL"
                raise ValueError(msg)
    except ValueError:
        # 校验失败直接抛出
        raise
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "authentication" in error_msg.lower() or "api key" in error_msg.lower():
            msg = f"Invalid API key for {provider}"
            raise ValueError(msg) from e
        # 其他错误视为网络/环境问题，允许继续保存
        return


def get_language_model_options(
    user_id: UUID | str | None = None, *, tool_calling: bool | None = None
) -> list[dict[str, Any]]:
    """返回可用语言模型选项列表（含运行时元数据）。

    契约：返回适配 UI 的 `options` 列表，包含 `name/icon/category/metadata`。
    关键路径（三步）：
    1) 获取统一模型元数据并按 `tool_calling` 过滤
    2) 结合用户禁用/启用模型与凭据过滤可见项
    3) 组装 UI 选项并补齐运行时参数
    异常流：变量服务不可用时回退为全量显示。
    排障入口：检查 `__disabled_models__`/`__enabled_models__` 与凭据变量。
    """
    # 获取 LLM 模型（默认排除 embeddings/弃用/不支持）
    # 若指定 tool_calling 则额外过滤
    if tool_calling is not None:
        all_models = get_unified_models_detailed(
            model_type="llm",
            include_deprecated=False,
            include_unsupported=False,
            tool_calling=tool_calling,
        )
    else:
        all_models = get_unified_models_detailed(
            model_type="llm",
            include_deprecated=False,
            include_unsupported=False,
        )

    # 获取用户级禁用/显式启用模型
    disabled_models = set()
    explicitly_enabled_models = set()
    if user_id:
        try:

            async def _get_model_status():
                async with session_scope() as session:
                    variable_service = get_variable_service()
                    if variable_service is None:
                        return set(), set()
                    from langflow.services.variable.service import DatabaseVariableService

                    if not isinstance(variable_service, DatabaseVariableService):
                        return set(), set()
                    all_vars = await variable_service.get_all(
                        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
                        session=session,
                    )
                    disabled = set()
                    enabled = set()
                    import json

                    for var in all_vars:
                        if var.name == "__disabled_models__" and var.value:
                            with contextlib.suppress(json.JSONDecodeError, TypeError):
                                disabled = set(json.loads(var.value))
                        elif var.name == "__enabled_models__" and var.value:
                            with contextlib.suppress(json.JSONDecodeError, TypeError):
                                enabled = set(json.loads(var.value))
                    return disabled, enabled

            disabled_models, explicitly_enabled_models = run_until_complete(_get_model_status())
        except Exception:  # noqa: BLE001, S110
                    # 获取失败则不进行过滤
            pass

    # 获取已配置凭据的提供方
    enabled_providers = set()
    if user_id:
        try:

            async def _get_enabled_providers():
                async with session_scope() as session:
                    variable_service = get_variable_service()
                    if variable_service is None:
                        return set()
                    from langflow.services.variable.constants import CREDENTIAL_TYPE
                    from langflow.services.variable.service import DatabaseVariableService

                    if not isinstance(variable_service, DatabaseVariableService):
                        return set()
                    all_vars = await variable_service.get_all(
                        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
                        session=session,
                    )
                    credential_names = {var.name for var in all_vars if var.type == CREDENTIAL_TYPE}
                    provider_variable_map = get_model_provider_variable_mapping()
                    return {
                        provider for provider, var_name in provider_variable_map.items() if var_name in credential_names
                    }

            enabled_providers = run_until_complete(_get_enabled_providers())
        except Exception:  # noqa: BLE001, S110
                    # 获取失败则不限制提供方
            pass

    options = []
    model_class_mapping = {
        "OpenAI": "ChatOpenAI",
        "Anthropic": "ChatAnthropic",
        "Google Generative AI": "ChatGoogleGenerativeAIFixed",
        "Ollama": "ChatOllama",
        "IBM WatsonX": "ChatWatsonx",
    }

    api_key_param_mapping = {
        "OpenAI": "api_key",
        "Anthropic": "api_key",
        "Google Generative AI": "google_api_key",
        "Ollama": "base_url",
        "IBM WatsonX": "apikey",
    }

    # 记录有模型的提供方
    providers_with_models = set()

    for provider_data in all_models:
        provider = provider_data.get("provider")
        models = provider_data.get("models", [])
        icon = provider_data.get("icon", "Bot")

        # 判断提供方是否启用
        is_provider_enabled = not user_id or not enabled_providers or provider in enabled_providers

        # 记录启用的提供方
        if is_provider_enabled:
            providers_with_models.add(provider)

        # 若指定 user_id 且提供方未启用则跳过
        if user_id and enabled_providers and provider not in enabled_providers:
            continue

        for model_data in models:
            model_name = model_data.get("model_name")
            metadata = model_data.get("metadata", {})
            is_default = metadata.get("default", False)

            # 可见性规则：
            # - 非默认且未显式启用则跳过
            # - 位于禁用列表则跳过
            # - 其余情况显示
            if not is_default and model_name not in explicitly_enabled_models:
                continue
            if model_name in disabled_models:
                continue

            # 组装选项字典
            option = {
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": {
                    "context_length": 128000,  # 默认值，可被覆盖
                    "model_class": model_class_mapping.get(provider, "ChatOpenAI"),
                    "model_name_param": "model",
                    "api_key_param": api_key_param_mapping.get(provider, "api_key"),
                },
            }

            # 为 OpenAI 推理模型追加列表
            if provider == "OpenAI" and metadata.get("reasoning"):
                if "reasoning_models" not in option["metadata"]:
                    option["metadata"]["reasoning_models"] = []
                option["metadata"]["reasoning_models"].append(model_name)

            # Ollama 追加 base_url 参数名
            if provider == "Ollama":
                option["metadata"]["base_url_param"] = "base_url"

            # WatsonX 追加参数名
            if provider == "IBM WatsonX":
                option["metadata"]["model_name_param"] = "model_id"
                option["metadata"]["url_param"] = "url"
                option["metadata"]["project_id_param"] = "project_id"

            options.append(option)

    # 追加“未启用提供方”占位项
    if user_id:
        for provider, metadata in model_provider_metadata.items():
            if provider not in providers_with_models:
                # 该提供方暂无可用模型，用占位项提示启用
                options.append(
                    {
                        "name": f"__enable_provider_{provider}__",
                        "icon": metadata.get("icon", "Bot"),
                        "category": provider,
                        "provider": provider,
                        "metadata": {
                            "is_disabled_provider": True,
                            "variable_name": metadata.get("variable_name"),
                        },
                    }
                )

    return options


def get_embedding_model_options(user_id: UUID | str | None = None) -> list[dict[str, Any]]:
    """返回可用嵌入模型选项列表（含运行时元数据）。"""
    # 获取嵌入模型（默认排除弃用/不支持）
    all_models = get_unified_models_detailed(
        model_type="embeddings",
        include_deprecated=False,
        include_unsupported=False,
    )

    # 获取用户级禁用/显式启用模型
    disabled_models = set()
    explicitly_enabled_models = set()
    if user_id:
        try:

            async def _get_model_status():
                async with session_scope() as session:
                    variable_service = get_variable_service()
                    if variable_service is None:
                        return set(), set()
                    from langflow.services.variable.service import DatabaseVariableService

                    if not isinstance(variable_service, DatabaseVariableService):
                        return set(), set()
                    all_vars = await variable_service.get_all(
                        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
                        session=session,
                    )
                    disabled = set()
                    enabled = set()
                    import json

                    for var in all_vars:
                        if var.name == "__disabled_models__" and var.value:
                            with contextlib.suppress(json.JSONDecodeError, TypeError):
                                disabled = set(json.loads(var.value))
                        elif var.name == "__enabled_models__" and var.value:
                            with contextlib.suppress(json.JSONDecodeError, TypeError):
                                enabled = set(json.loads(var.value))
                    return disabled, enabled

            disabled_models, explicitly_enabled_models = run_until_complete(_get_model_status())
        except Exception:  # noqa: BLE001, S110
            # 获取失败则不进行过滤
            pass

    # 获取已配置凭据的提供方
    enabled_providers = set()
    if user_id:
        try:

            async def _get_enabled_providers():
                async with session_scope() as session:
                    variable_service = get_variable_service()
                    if variable_service is None:
                        return set()
                    from langflow.services.variable.constants import CREDENTIAL_TYPE
                    from langflow.services.variable.service import DatabaseVariableService

                    if not isinstance(variable_service, DatabaseVariableService):
                        return set()
                    all_vars = await variable_service.get_all(
                        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
                        session=session,
                    )
                    credential_names = {var.name for var in all_vars if var.type == CREDENTIAL_TYPE}
                    provider_variable_map = get_model_provider_variable_mapping()
                    return {
                        provider for provider, var_name in provider_variable_map.items() if var_name in credential_names
                    }

            enabled_providers = run_until_complete(_get_enabled_providers())
        except Exception:  # noqa: BLE001, S110
            # 获取失败则不限制提供方
            pass

    options = []
    embedding_class_mapping = {
        "OpenAI": "OpenAIEmbeddings",
        "Google Generative AI": "GoogleGenerativeAIEmbeddings",
        "Ollama": "OllamaEmbeddings",
        "IBM WatsonX": "WatsonxEmbeddings",
    }

    # 提供方参数映射
    param_mappings = {
        "OpenAI": {
            "model": "model",
            "api_key": "api_key",
            "api_base": "base_url",
            "dimensions": "dimensions",
            "chunk_size": "chunk_size",
            "request_timeout": "timeout",
            "max_retries": "max_retries",
            "show_progress_bar": "show_progress_bar",
            "model_kwargs": "model_kwargs",
        },
        "Google Generative AI": {
            "model": "model",
            "api_key": "google_api_key",
            "request_timeout": "request_options",
            "model_kwargs": "client_options",
        },
        "Ollama": {
            "model": "model",
            "base_url": "base_url",
            "num_ctx": "num_ctx",
            "request_timeout": "request_timeout",
            "model_kwargs": "model_kwargs",
        },
        "IBM WatsonX": {
            "model_id": "model_id",
            "url": "url",
            "api_key": "apikey",
            "project_id": "project_id",
            "space_id": "space_id",
            "request_timeout": "request_timeout",
        },
    }

    # 记录有模型的提供方
    providers_with_models = set()

    for provider_data in all_models:
        provider = provider_data.get("provider")
        models = provider_data.get("models", [])
        icon = provider_data.get("icon", "Bot")

        # 判断提供方是否启用
        is_provider_enabled = not user_id or not enabled_providers or provider in enabled_providers

        # 记录启用的提供方
        if is_provider_enabled:
            providers_with_models.add(provider)

        # 若指定 user_id 且提供方未启用则跳过
        if user_id and enabled_providers and provider not in enabled_providers:
            continue

        for model_data in models:
            model_name = model_data.get("model_name")
            metadata = model_data.get("metadata", {})
            is_default = metadata.get("default", False)

            # 可见性规则：
            # - 非默认且未显式启用则跳过
            # - 位于禁用列表则跳过
            # - 其余情况显示
            if not is_default and model_name not in explicitly_enabled_models:
                continue
            if model_name in disabled_models:
                continue

            # 组装选项字典
            option = {
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": {
                    "embedding_class": embedding_class_mapping.get(provider, "OpenAIEmbeddings"),
                    "param_mapping": param_mappings.get(provider, param_mappings["OpenAI"]),
                    "model_type": "embeddings",  # 标记为嵌入模型
                },
            }

            options.append(option)

    # 追加“未启用提供方”占位项
    if user_id:
        for provider, metadata in model_provider_metadata.items():
            if provider not in providers_with_models and provider in embedding_class_mapping:
                # 该提供方暂无可用模型，用占位项提示启用
                options.append(
                    {
                        "name": f"__enable_provider_{provider}__",
                        "icon": metadata.get("icon", "Bot"),
                        "category": provider,
                        "provider": provider,
                        "metadata": {
                            "is_disabled_provider": True,
                            "variable_name": metadata.get("variable_name"),
                        },
                    }
                )

    return options


def normalize_model_names_to_dicts(model_names: list[str] | str) -> list[dict[str, Any]]:
    """将模型名（字符串/列表）规范化为字典列表。"""
    # 单个字符串转为列表
    if isinstance(model_names, str):
        model_names = [model_names]

    # 获取模型元数据用于补全
    try:
        all_models = get_unified_models_detailed()
    except Exception:  # noqa: BLE001
        # 元数据不可用时返回最小结构
        return [{"name": name} for name in model_names]

    # 运行时模型类映射
    model_class_mapping = {
        "OpenAI": "ChatOpenAI",
        "Anthropic": "ChatAnthropic",
        "Google Generative AI": "ChatGoogleGenerativeAIFixed",
        "Ollama": "ChatOllama",
        "IBM WatsonX": "ChatWatsonx",
    }

    api_key_param_mapping = {
        "OpenAI": "api_key",
        "Anthropic": "api_key",
        "Google Generative AI": "google_api_key",
        "Ollama": "base_url",
        "IBM WatsonX": "apikey",
    }

    # 构建 model_name -> 运行时元数据映射
    model_lookup = {}
    for provider_data in all_models:
        provider = provider_data.get("provider")
        icon = provider_data.get("icon", "Bot")
        for model_data in provider_data.get("models", []):
            model_name = model_data.get("model_name")
            base_metadata = model_data.get("metadata", {})

            # 构建运行时元数据（与 get_language_model_options 保持一致）
            runtime_metadata = {
                "context_length": 128000,  # 默认值
                "model_class": model_class_mapping.get(provider, "ChatOpenAI"),
                "model_name_param": "model",
                "api_key_param": api_key_param_mapping.get(provider, "api_key"),
            }

            # OpenAI 推理模型追加列表
            if provider == "OpenAI" and base_metadata.get("reasoning"):
                runtime_metadata["reasoning_models"] = [model_name]

            # Ollama 追加 base_url 参数名
            if provider == "Ollama":
                runtime_metadata["base_url_param"] = "base_url"

            # WatsonX 追加参数名
            if provider == "IBM WatsonX":
                runtime_metadata["model_name_param"] = "model_id"
                runtime_metadata["url_param"] = "url"
                runtime_metadata["project_id_param"] = "project_id"

            # 合并基础元数据与运行时元数据
            full_metadata = {**base_metadata, **runtime_metadata}

            model_lookup[model_name] = {
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": full_metadata,
            }

    # 转换为目标字典列表
    result = []
    for name in model_names:
        if name in model_lookup:
            result.append(model_lookup[name])
        else:
            # 注册表中未找到时返回最小结构
            result.append(
                {
                    "name": name,
                    "provider": "Unknown",
                    "metadata": {
                        "model_class": "ChatOpenAI",  # 默认回退
                        "model_name_param": "model",
                        "api_key_param": "api_key",
                    },
                }
            )

    return result


def get_llm(
    model,
    user_id: UUID | str | None,
    api_key=None,
    temperature=None,
    *,
    stream=False,
    watsonx_url=None,
    watsonx_project_id=None,
    ollama_base_url=None,
) -> Any:
    """根据选中的模型配置构建 LLM 实例。

    契约：返回可调用的模型实例；必要参数缺失时抛 `ValueError`。
    关键路径（三步）：
    1) 解析模型选择与提供方元数据
    2) 获取 API key 并组装参数
    3) 实例化模型类并返回
    异常流：缺失 API key 或必要参数会抛 `ValueError`。
    排障入口：关注提供方变量名与模型元数据字段映射。
    """
    # 若已是 BaseLanguageModel 实例则直接返回
    try:
        from langchain_core.language_models import BaseLanguageModel

        if isinstance(model, BaseLanguageModel):
            # 已实例化，直接返回
            return model
    except ImportError:
        pass

    # 解析模型选择
    if not model or not isinstance(model, list) or len(model) == 0:
        msg = "A model selection is required"
        raise ValueError(msg)

    # 仅使用第一个模型（当前只支持单选）
    model = model[0]

    # 读取模型元数据
    model_name = model.get("name")
    provider = model.get("provider")
    metadata = model.get("metadata", {})

    # 读取模型类与参数名
    api_key_param = metadata.get("api_key_param", "api_key")

    # 获取 API key（用户输入或全局变量）
    api_key = get_api_key_for_provider(user_id, provider, api_key)

    # 校验 API key（Ollama 不需要）
    if not api_key and provider != "Ollama":
        # 获取提供方变量名用于提示
        provider_variable_map = get_model_provider_variable_mapping()
        variable_name = provider_variable_map.get(provider, f"{provider.upper().replace(' ', '_')}_API_KEY")
        msg = (
            f"{provider} API key is required when using {provider} provider. "
            f"Please provide it in the component or configure it globally as {variable_name}."
        )
        raise ValueError(msg)

    # 获取模型类
    model_class = get_model_classes().get(metadata.get("model_class"))
    if model_class is None:
        msg = f"No model class defined for {model_name}"
        raise ValueError(msg)
    model_name_param = metadata.get("model_name_param", "model")

    # 推理模型不支持温度参数
    reasoning_models = metadata.get("reasoning_models", [])
    if model_name in reasoning_models:
        temperature = None

    # 组装参数
    kwargs = {
        model_name_param: model_name,
        "streaming": stream,
        api_key_param: api_key,
    }

    if temperature is not None:
        kwargs["temperature"] = temperature

    # 提供方特定参数
    if provider == "IBM WatsonX":
        # WatsonX 需要 url 与 project_id
        # 若未提供则交由 ChatWatsonx 抛出原生错误
        # 允许缺少字段的组件优雅失败

        url_param = metadata.get("url_param", "url")
        project_id_param = metadata.get("project_id_param", "project_id")

        has_url = watsonx_url is not None
        has_project_id = watsonx_project_id is not None

        if has_url and has_project_id:
            # 两者齐备，写入参数
            kwargs[url_param] = watsonx_url
            kwargs[project_id_param] = watsonx_project_id
        elif has_url or has_project_id:
            # 仅提供其一，视为配置错误
            missing = "project ID" if has_url else "URL"
            provided = "URL" if has_url else "project ID"
            msg = (
                f"IBM WatsonX requires both a URL and project ID. "
                f"You provided a watsonx {provided} but no {missing}. "
                f"Please add a 'watsonx {missing.title()}' field to your component or use the Language Model component "
                f"which fully supports IBM WatsonX configuration."
            )
            raise ValueError(msg)
        # else: 两者均缺失，交由 ChatWatsonx 处理
    elif provider == "Ollama" and ollama_base_url:
        # Ollama 使用自定义 base_url
        base_url_param = metadata.get("base_url_param", "base_url")
        kwargs[base_url_param] = ollama_base_url

    try:
        return model_class(**kwargs)
    except Exception as e:
        # WatsonX 初始化失败时提供额外提示
        if provider == "IBM WatsonX" and ("url" in str(e).lower() or "project" in str(e).lower()):
            msg = (
                f"Failed to initialize IBM WatsonX model: {e}\n\n"
                "IBM WatsonX requires additional configuration parameters (API endpoint URL and project ID). "
                "This component may not support these parameters. "
                "Consider using the 'Language Model' component instead, which fully supports IBM WatsonX."
            )
            raise ValueError(msg) from e
        # 其他情况直接抛出原异常
        raise


def update_model_options_in_build_config(
    component: Any,
    build_config: dict,
    cache_key_prefix: str,
    get_options_func: Callable,
    field_name: str | None = None,
    field_value: Any = None,
) -> dict:
    """更新 build_config 中的模型选项并使用缓存。

    关键路径（三步）：
    1) 判断是否需要刷新缓存（初始加载/字段变化/缓存过期）
    2) 调用 `get_options_func` 获取选项并缓存
    3) 写回 build_config 并设置默认模型与可见性
    失败语义：选项获取失败时回退为空列表。
    性能瓶颈：变量服务查询与选项构建。
    排障入口：检查 `component.cache` 与 `is_refresh` 标志。
    """
    import time

    # 基于 user_id 的缓存键
    cache_key = f"{cache_key_prefix}_{component.user_id}"
    cache_timestamp_key = f"{cache_key}_timestamp"
    cache_ttl = 30  # 30 秒 TTL 以更快捕捉变量变化

    # 判断缓存是否过期
    cache_expired = False
    if cache_timestamp_key in component.cache:
        time_since_cache = time.time() - component.cache[cache_timestamp_key]
        cache_expired = time_since_cache > cache_ttl

    # 前端刷新请求标记
    is_refresh_request = build_config.get("is_refresh", False)

    # 判断是否需要刷新
    should_refresh = (
        field_name == "api_key"  # API key 变化
        or field_name is None  # 初次加载
        or field_name == "model"  # 模型字段刷新按钮触发
        or cache_key not in component.cache  # 缓存未命中
        or cache_expired  # 缓存过期
        or is_refresh_request  # 前端请求刷新
    )

    if should_refresh:
        # 根据用户可用模型获取选项
        try:
            options = get_options_func(user_id=component.user_id)
            # 缓存结果与时间戳
            component.cache[cache_key] = {"options": options}
            component.cache[cache_timestamp_key] = time.time()
        except KeyError as exc:
            # 获取失败则回退为空
            component.log("Failed to fetch user-specific model options: %s", exc)
            component.cache[cache_key] = {"options": []}
            component.cache[cache_timestamp_key] = time.time()

    # 使用缓存结果
    cached = component.cache.get(cache_key, {"options": []})
    build_config["model"]["options"] = cached["options"]

    # 初次加载或模型字段为空时设置默认值
    # 仅在初始加载或模型字段被设置且为空时生效
    # 获取当前值用于判断是否为空
    current_model_value = build_config.get("model", {}).get("value")
    model_is_empty = not current_model_value or current_model_value == "" or current_model_value == []
    should_set_default = field_name is None or (field_name == "model" and model_is_empty)
    if should_set_default:
        options = cached.get("options", [])
        if options:
            # 根据前缀判断模型类型
            model_type = "embeddings" if cache_key_prefix == "embedding_model_options" else "language"

            # 尝试从变量服务获取用户默认模型
            default_model_name = None
            default_model_provider = None
            try:

                async def _get_default_model():
                    async with session_scope() as session:
                        variable_service = get_variable_service()
                        if variable_service is None:
                            return None, None
                        from langflow.services.variable.service import DatabaseVariableService

                        if not isinstance(variable_service, DatabaseVariableService):
                            return None, None

                        # 变量名与 API 保持一致
                        var_name = (
                            "__default_embedding_model__"
                            if model_type == "embeddings"
                            else "__default_language_model__"
                        )

                        try:
                            var = await variable_service.get_variable_object(
                                user_id=UUID(component.user_id)
                                if isinstance(component.user_id, str)
                                else component.user_id,
                                name=var_name,
                                session=session,
                            )
                            if var and var.value:
                                import json

                                parsed_value = json.loads(var.value)
                                if isinstance(parsed_value, dict):
                                    return parsed_value.get("model_name"), parsed_value.get("provider")
                        except (ValueError, json.JSONDecodeError, TypeError):
                            # 变量不存在或格式不正确
                            logger.info("Variable not found or invalid format", exc_info=True)
                        return None, None

                default_model_name, default_model_provider = run_until_complete(_get_default_model())
            except Exception:  # noqa: BLE001
                # 获取默认模型失败则继续
                logger.info("Failed to get default model, continue without it", exc_info=True)

            # 在选项中查找默认模型
            default_model = None
            if default_model_name and default_model_provider:
                # 用户偏好优先
                for opt in options:
                    if opt.get("name") == default_model_name and opt.get("provider") == default_model_provider:
                        default_model = opt
                        break

            # 用户默认未命中时回退第一个选项
            if not default_model and options:
                default_model = options[0]

            # 写入默认值
            if default_model:
                build_config["model"]["value"] = [default_model]

    # 可见性逻辑：
    # - 仅当 field_value 为 "connect_other_models" 时显示 handle
    # - 其他情况隐藏
    if field_value == "connect_other_models":
        # 显式选择“连接其他模型”
        if cache_key_prefix == "embedding_model_options":
            build_config["model"]["input_types"] = ["Embeddings"]
        else:
            build_config["model"]["input_types"] = ["LanguageModel"]
    else:
        # 默认或选择模型时隐藏
        build_config["model"]["input_types"] = []

    return build_config
