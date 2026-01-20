"""
模块名称：AI/ML API 模型拉取与分类

本模块提供从外部 AI/ML API 拉取模型列表并按类型分类的能力，主要用于在未内置元数据的
情况下动态获取模型名集合。
主要功能包括：
- 从固定 API 地址拉取模型数据
- 按模型类型拆分到不同的列表

关键组件：
- `AimlModels`：拉取与分类模型的容器

设计背景：当模型列表由外部服务维护时，需要在运行期获取并分类。
注意事项：网络/解析失败会抛出异常，上层需处理重试或降级。
"""

import httpx
from openai import APIConnectionError, APIError


class AimlModels:
    """AI/ML API 模型拉取与分类容器。

    契约：`get_aiml_models()` 执行后，模型 ID 会被填充到各类型列表。
    副作用：产生外部 HTTP 请求并修改实例的模型列表。
    失败语义：网络或状态码异常会抛 `APIConnectionError` / `APIError`；
    响应解析失败会抛 `ValueError`。
    """

    def __init__(self):
        self.chat_models = []
        self.image_models = []
        self.embedding_models = []
        self.stt_models = []
        self.tts_models = []
        self.language_models = []

    def get_aiml_models(self):
        """拉取模型列表并按类型分类到实例字段。

        关键路径（三步）：
        1) 请求远端模型列表并处理网络/HTTP 错误
        2) 解析响应 JSON 并提取 `data`
        3) 调用 `separate_models_by_type` 分类模型
        异常流：网络异常/HTTP 非 2xx/解析失败将直接抛出。
        性能瓶颈：主要由远端 API 响应延迟决定。
        排障入口：关注异常类型与状态码信息以区分网络与数据问题。
        决策：异常对齐 OpenAI 生态错误类型
        问题：调用方需要统一处理连接与 HTTP 错误
        方案：将网络错误转换为 `APIConnectionError`/`APIError`
        代价：错误类型固定，丢失部分底层异常细节
        重评：当上层需要更细粒度错误时考虑保留原始异常
        """

        try:
            with httpx.Client() as client:
                response = client.get("https://api.aimlapi.com/models")
                response.raise_for_status()
        except httpx.RequestError as e:
            msg = "Failed to connect to the AI/ML API."
            raise APIConnectionError(msg) from e
        except httpx.HTTPStatusError as e:
            msg = f"AI/ML API responded with status code: {e.response.status_code}"
            raise APIError(
                message=msg,
                body=None,
                request=e.request,
            ) from e

        try:
            models = response.json().get("data", [])
            self.separate_models_by_type(models)
        except (ValueError, KeyError, TypeError) as e:
            msg = "Failed to parse response data from AI/ML API. The format may be incorrect."
            raise ValueError(msg) from e

    def separate_models_by_type(self, models):
        """按模型 `type` 将模型 ID 归入不同列表。

        契约：`models` 为可迭代的字典对象列表，至少包含 `type` 与 `id` 字段。
        副作用：更新实例中的分类列表。
        失败语义：缺失字段时会导致 `None` 被忽略，不抛异常。
        决策：未知类型直接忽略
        问题：外部 API 可能新增未识别的类型
        方案：仅处理已知类型映射
        代价：新类型不会被记录
        重评：当出现新类型需求时扩展映射表
        """

        model_type_mapping = {
            "chat-completion": self.chat_models,
            "image": self.image_models,
            "embedding": self.embedding_models,
            "stt": self.stt_models,
            "tts": self.tts_models,
            "language-completion": self.language_models,
        }

        for model in models:
            model_type = model.get("type")
            model_id = model.get("id")
            if model_type in model_type_mapping:
                model_type_mapping[model_type].append(model_id)
