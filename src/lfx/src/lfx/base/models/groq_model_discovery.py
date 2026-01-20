"""
模块名称：Groq 模型动态发现与工具调用检测

本模块通过 Groq API 动态拉取模型列表，并自动测试工具调用能力，
以降低人工维护元数据的成本。
主要功能包括：
- 拉取可用模型并区分 LLM/非 LLM
- 以最小调用测试工具调用能力
- 结果缓存 24 小时以降低 API 压力

关键组件：
- `GroqModelDiscovery`：动态发现与缓存管理
- `get_groq_models`：对外便捷接口

设计背景：Groq 模型列表变化频繁，需要自动发现与验证能力支持。
注意事项：无 API key 时会退回兜底列表。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from lfx.log.logger import logger


class GroqModelDiscovery:
    """动态发现并缓存 Groq 模型能力。"""

    # 缓存文件位置：位于 models 子目录的本地缓存
    CACHE_FILE = Path(__file__).parent / ".cache" / "groq_models_cache.json"
    CACHE_DURATION = timedelta(hours=24)  # 每 24 小时刷新一次

    # 需从 LLM 列表排除的模式（音频/TTS/安全模型）
    SKIP_PATTERNS = ["whisper", "tts", "guard", "safeguard", "prompt-guard", "saba"]

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.groq.com"):
        """初始化发现器，可选传入 API key。

        契约：`api_key` 为 None 时仅使用缓存或兜底列表。
        副作用：无。
        """
        self.api_key = api_key
        self.base_url = base_url

    def get_models(self, *, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """获取模型元数据并检测工具调用能力。

        关键路径（三步）：
        1) 读取缓存（若未强制刷新）
        2) 拉取模型列表并区分 LLM/非 LLM
        3) 测试 LLM 工具调用并保存缓存
        异常流：网络/解析错误会回退到兜底列表。
        性能瓶颈：逐模型工具调用测试。
        排障入口：日志 `Using cached Groq model metadata` 与异常堆栈。
        """
        # 优先尝试缓存
        if not force_refresh:
            cached = self._load_cache()
            if cached:
                logger.info("Using cached Groq model metadata")
                return cached

        # 拉取最新数据
        if not self.api_key:
            logger.warning("No API key provided, using minimal fallback list")
            return self._get_fallback_models()

        try:
            models_metadata = {}

            # 步骤 1：获取可用模型列表
            available_models = self._fetch_available_models()
            logger.info(f"Found {len(available_models)} models from Groq API")

            # 步骤 2：区分 LLM 与非 LLM
            llm_models = []
            non_llm_models = []

            for model_id in available_models:
                if any(pattern in model_id.lower() for pattern in self.SKIP_PATTERNS):
                    non_llm_models.append(model_id)
                else:
                    llm_models.append(model_id)

            # 步骤 3：测试 LLM 工具调用能力
            logger.info(f"Testing {len(llm_models)} LLM models for tool calling support...")
            for model_id in llm_models:
                supports_tools = self._test_tool_calling(model_id)
                models_metadata[model_id] = {
                    "name": model_id,
                    "provider": self._get_provider_name(model_id),
                    "tool_calling": supports_tools,
                    "preview": "preview" in model_id.lower() or "/" in model_id,
                    "last_tested": datetime.now(timezone.utc).isoformat(),
                }
                logger.debug(f"{model_id}: tool_calling={supports_tools}")

            # 步骤 4：非 LLM 标记为不支持
            for model_id in non_llm_models:
                models_metadata[model_id] = {
                    "name": model_id,
                    "provider": self._get_provider_name(model_id),
                    "not_supported": True,
                    "last_tested": datetime.now(timezone.utc).isoformat(),
                }

            # 保存缓存
            self._save_cache(models_metadata)

        except (requests.RequestException, KeyError, ValueError, ImportError) as e:
            logger.exception(f"Error discovering models: {e}")
            return self._get_fallback_models()
        else:
            return models_metadata

    def _fetch_available_models(self) -> list[str]:
        """从 Groq API 拉取可用模型列表。"""
        url = f"{self.base_url}/openai/v1/models"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        model_list = response.json()
        # 使用直接访问，缺少 data 时抛 KeyError
        return [model["id"] for model in model_list["data"]]

    def _test_tool_calling(self, model_id: str) -> bool:
        """测试模型是否支持工具调用。"""
        try:
            import groq

            client = groq.Groq(api_key=self.api_key)

            # 简单工具定义
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "test_tool",
                        "description": "A test tool",
                        "parameters": {
                            "type": "object",
                            "properties": {"x": {"type": "string"}},
                            "required": ["x"],
                        },
                    },
                }
            ]

            messages = [{"role": "user", "content": "test"}]

            # 发起带工具调用的请求
            client.chat.completions.create(
                model=model_id, messages=messages, tools=tools, tool_choice="auto", max_tokens=10
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            error_msg = str(e).lower()
            # 如果错误提示与工具调用相关，则视为不支持
            if "tool" in error_msg:
                return False
            # 其余错误可能为限流等，保守返回 False
            logger.warning(f"Error testing {model_id}: {e}")
            return False
        else:
            return True

    def _get_provider_name(self, model_id: str) -> str:
        """从模型 ID 推断提供方名称。"""
        if "/" in model_id:
            provider_map = {
                "meta-llama": "Meta",
                "openai": "OpenAI",
                "groq": "Groq",
                "moonshotai": "Moonshot AI",
                "qwen": "Alibaba Cloud",
            }
            prefix = model_id.split("/")[0]
            return provider_map.get(prefix, prefix.title())

        # 常见前缀规则
        if model_id.startswith("llama"):
            return "Meta"
        if model_id.startswith("qwen"):
            return "Alibaba Cloud"
        if model_id.startswith("allam"):
            return "SDAIA"

        return "Groq"

    def _load_cache(self) -> dict[str, dict] | None:
        """加载缓存并校验有效期。"""
        if not self.CACHE_FILE.exists():
            return None

        try:
            with self.CACHE_FILE.open() as f:
                cache_data = json.load(f)

            # 校验缓存时间
            cache_time = datetime.fromisoformat(cache_data["cached_at"])
            if datetime.now(timezone.utc) - cache_time > self.CACHE_DURATION:
                logger.info("Cache expired, will fetch fresh data")
                return None

            return cache_data["models"]

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Invalid cache file: {e}")
            return None

    def _save_cache(self, models_metadata: dict[str, dict]) -> None:
        """保存模型元数据到缓存。"""
        try:
            cache_data = {"cached_at": datetime.now(timezone.utc).isoformat(), "models": models_metadata}

            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self.CACHE_FILE.open("w") as f:
                json.dump(cache_data, f, indent=2)

            logger.info(f"Cached {len(models_metadata)} models to {self.CACHE_FILE}")

        except (OSError, TypeError, ValueError) as e:
            logger.warning(f"Failed to save cache: {e}")

    def _get_fallback_models(self) -> dict[str, dict]:
        """API 不可用时返回最小兜底列表。"""
        return {
            "llama-3.1-8b-instant": {
                "name": "llama-3.1-8b-instant",
                "provider": "Meta",
                "tool_calling": True,
                "preview": False,
            },
            "llama-3.3-70b-versatile": {
                "name": "llama-3.3-70b-versatile",
                "provider": "Meta",
                "tool_calling": True,
                "preview": False,
            },
        }


# 对外便捷接口
def get_groq_models(api_key: str | None = None, *, force_refresh: bool = False) -> dict[str, dict]:
    """获取 Groq 模型元数据（含工具调用能力）。"""
    discovery = GroqModelDiscovery(api_key=api_key)
    return discovery.get_models(force_refresh=force_refresh)
