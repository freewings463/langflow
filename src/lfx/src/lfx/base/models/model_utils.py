"""
模块名称：模型工具函数

本模块提供模型相关的通用工具函数，主要用于模型名称推断与 Ollama 端点/模型列表查询。
主要功能包括：
- 从模型实例中推断模型名称
- 校验 Ollama API 地址可达性
- 拉取 Ollama 模型并按能力过滤

关键组件：
- `get_model_name`
- `is_valid_ollama_url`
- `get_ollama_models`

设计背景：统一模型工具逻辑，避免各组件重复实现。
注意事项：Ollama 相关函数会产生网络请求，需在异步上下文中使用。
"""

import asyncio
from urllib.parse import urljoin

import httpx

from lfx.log.logger import logger
from lfx.utils.util import transform_localhost_url

HTTP_STATUS_OK = 200


def get_model_name(llm, display_name: str | None = "Custom"):
    """从模型实例中推断可读的模型名称。

    契约：优先读取 `model_name`/`model`/`model_id`/`deployment_name` 属性，
    若不存在则回退到 `display_name`。
    副作用：无。
    失败语义：缺失属性不会抛错，直接回退。
    """
    attributes_to_check = ["model_name", "model", "model_id", "deployment_name"]

    # 使用第一个可用属性作为模型名称
    model_name = next((getattr(llm, attr) for attr in attributes_to_check if hasattr(llm, attr)), None)

    # 若无匹配属性则回退到显示名
    return model_name if model_name is not None else display_name


async def is_valid_ollama_url(url: str) -> bool:
    """校验是否为可访问的 Ollama API 地址。

    契约：返回布尔值，`True` 表示可访问 `api/tags`。
    失败语义：网络异常返回 `False`，并记录调试日志。
    """
    try:
        url = transform_localhost_url(url)
        if not url:
            return False
        # 去除 `/v1` 后缀（Ollama API 在根路径）
        url = url.rstrip("/").removesuffix("/v1")
        if not url.endswith("/"):
            url = url + "/"
        async with httpx.AsyncClient() as client:
            return (await client.get(url=urljoin(url, "api/tags"))).status_code == HTTP_STATUS_OK
    except httpx.RequestError:
        logger.debug(f"Invalid Ollama URL: {url}")
        return False


async def get_ollama_models(
    base_url_value: str, desired_capability: str, json_models_key: str, json_name_key: str, json_capabilities_key: str
) -> list[str]:
    """从 Ollama API 拉取模型并按能力过滤。

    契约：返回支持 `desired_capability` 的模型名列表（排序后）。
    关键路径（三步）：
    1) 规范化 `base_url` 并请求 `api/tags`
    2) 逐个请求 `api/show` 获取能力列表
    3) 过滤出包含 `desired_capability` 的模型名
    异常流：网络/解析错误抛 `ValueError`。
    性能瓶颈：每个模型一次 `api/show` 请求。
    排障入口：关注 `api/tags` 与 `api/show` 的响应内容与日志。
    """
    try:
        # 去除 `/v1` 后缀（Ollama API 在根路径）
        base_url = base_url_value.rstrip("/").removesuffix("/v1")
        if not base_url.endswith("/"):
            base_url = base_url + "/"
        base_url = transform_localhost_url(base_url)

        # Ollama REST API：模型列表
        tags_url = urljoin(base_url, "api/tags")

        # Ollama REST API：模型能力
        show_url = urljoin(base_url, "api/show")
        tags_response = None

        async with httpx.AsyncClient() as client:
            # 获取模型列表
            tags_response = await client.get(url=tags_url)
            tags_response.raise_for_status()
            models = tags_response.json()
            if asyncio.iscoroutine(models):
                models = await models
            await logger.adebug(f"Available models: {models}")

            # 过滤并仅保留具备目标能力的模型
            model_ids = []
            for model in models.get(json_models_key, []):
                model_name = model.get(json_name_key)
                if not model_name:
                    continue
                await logger.adebug(f"Checking model: {model_name}")

                payload = {"model": model_name}
                show_response = await client.post(url=show_url, json=payload)
                show_response.raise_for_status()
                json_data = show_response.json()
                if asyncio.iscoroutine(json_data):
                    json_data = await json_data

                capabilities = json_data.get(json_capabilities_key, [])
                await logger.adebug(f"Model: {model_name}, Capabilities: {capabilities}")

                if desired_capability in capabilities:
                    model_ids.append(model_name)

            return sorted(model_ids)

    except (httpx.RequestError, ValueError) as e:
        msg = "Could not get model names from Ollama."
        await logger.aexception(msg)
        raise ValueError(msg) from e
