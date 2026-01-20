"""
模块名称：aiml_embeddings

本模块提供 AIML Embeddings 的 HTTP 适配器，实现 Langflow 的 Embeddings 契约。
主要功能包括：
- 通过 AIML API 批量生成向量
- 对单条文本提供 query 级封装

关键组件：
- `AIMLEmbeddingsImpl`：AIML API 客户端实现

设计背景：第三方向量服务接入需要统一接口与错误语义
使用场景：在 Langflow 中选用 AIML 模型生成向量
注意事项：当前按单条并发请求，受限于外部限流
"""

import concurrent.futures
import json

import httpx
from pydantic import BaseModel, SecretStr

from lfx.field_typing import Embeddings
from lfx.log.logger import logger


class AIMLEmbeddingsImpl(BaseModel, Embeddings):
    """AIML Embeddings 适配实现。

    契约：需配置 `api_key` 与 `model`；返回的向量数量与输入一致。
    副作用：对外部 AIML API 发起网络请求。
    失败语义：HTTP/JSON 解析失败会抛出异常，调用方需处理重试或降级。
    决策：使用线程池并发单条请求。
    问题：批量接口返回不稳定且难以定位单条错误。
    方案：每条文本独立请求并汇总结果。
    代价：请求数线性增长，易触发限流。
    重评：当 AIML 提供稳定批量接口或更高限额时。
    """

    embeddings_completion_url: str = "https://api.aimlapi.com/v1/embeddings"

    api_key: SecretStr
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """并发调用 AIML 接口生成批量向量。

        契约：`texts` 为字符串列表；返回与输入等长的向量列表。
        副作用：创建线程池并发送多次 HTTP 请求。
        失败语义：HTTP/JSON/KeyError/ValueError 原样抛出；调用方可重试或降级。
        关键路径（三步）：
        1) 构造认证头并准备线程池。
        2) 并发调用 `_embed_text` 并校验 `data` 数量。
        3) 依据索引回填结果列表。
        性能瓶颈：高并发时受 API 限流与网络 RTT 影响。
        排障入口：日志关键字 `Error occurred` + 异常堆栈。
        决策：并发单条请求而非一次性批量。
        问题：批量失败时难以定位具体文本。
        方案：单条调用并在本地聚合。
        代价：请求数增加，吞吐受限。
        重评：当服务端支持稳定批量并提供错误明细时。
        """
        embeddings = [None] * len(texts)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
        }

        with httpx.Client() as client, concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for i, text in enumerate(texts):
                futures.append((i, executor.submit(self._embed_text, client, headers, text)))

            for index, future in futures:
                try:
                    result_data = future.result()
                    if len(result_data["data"]) != 1:
                        msg = f"Expected one embedding, got {len(result_data['data'])}"
                        raise ValueError(msg)
                    embeddings[index] = result_data["data"][0]["embedding"]
                except (
                    httpx.HTTPStatusError,
                    httpx.RequestError,
                    json.JSONDecodeError,
                    KeyError,
                    ValueError,
                ):
                    logger.exception("Error occurred")
                    raise

        return embeddings  # type: ignore[return-value]

    def _embed_text(self, client: httpx.Client, headers: dict, text: str) -> dict:
        """发送单条文本请求并返回 JSON。

        契约：入参需为已配置的 `httpx.Client` 与认证头。
        副作用：发起一次 HTTP 请求。
        失败语义：`raise_for_status` 与 JSON 解析异常原样抛出。
        """
        payload = {
            "model": self.model,
            "input": text,
        }
        response = client.post(
            self.embeddings_completion_url,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def embed_query(self, text: str) -> list[float]:
        """生成单条查询向量。

        契约：入参为单条文本；返回单个向量。
        副作用：调用 `embed_documents`，产生一次网络请求。
        失败语义：沿用 `embed_documents` 的异常语义。
        决策：复用批量路径保证向量一致性。
        问题：避免单/批逻辑分叉导致差异。
        方案：包装为单元素列表后复用。
        代价：多一次列表封装与索引开销。
        重评：当 AIML 提供专用单条低延迟接口时。
        """
        return self.embed_documents([text])[0]
