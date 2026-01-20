"""
模块名称：`Flow` 加载辅助工具

本模块提供从 `Langflow` 服务获取 `Flow` 元信息的便捷方法，并复用加载相关辅助函数。
主要功能包括：
- 通过 `HTTP` 拉取指定 `Flow` 并转换为 `FlowBase` 输出
- 复用 `lfx.load.utils` 中的上传/替换工具

关键组件：`get_flow`
设计背景：上层调用需要简化对 `Flow` 读取与格式化的流程。
使用场景：`CLI` 或服务在运行前读取 `Flow` 配置。
注意事项：未命中 `200` 状态码时返回 `None`，调用方需自行判定。
"""

import httpx
from lfx.load.utils import UploadError, replace_tweaks_with_env, upload, upload_file

from langflow.services.database.models.flow.model import FlowBase


def get_flow(url: str, flow_id: str):
    """从 `Langflow` 拉取 `Flow` 详情并转为字典。

    契约：
    - 输入：`url` 为服务根地址；`flow_id` 为目标 `Flow` 标识。
    - 输出：命中 `200` 时返回 `FlowBase` 的 `model_dump()` 字典；否则返回 `None`。
    - 副作用：发起一次 `HTTP GET` 请求。
    - 失败语义：请求异常抛 `UploadError`，异常信息包含 `Error retrieving flow`。

    关键路径（三步）：
    1) 拼接 `GET /api/v1/flows/{flow_id}` 地址。
    2) 发起 `httpx.get` 请求并校验状态码。
    3) 将响应转换为 `FlowBase` 并输出字典。

    决策：使用同步 `httpx.get` 在同步函数内拉取 `Flow`。
    问题：需要在同步调用链中获取远端 `Flow` 配置。
    方案：直接执行同步请求并在成功时转换为模型。
    代价：阻塞调用线程且无重试策略。
    重评：当调用方迁移为异步或需要并发拉取时改为异步客户端。

    排障入口：异常信息包含 `Error retrieving flow`，可用于定位调用失败。
    """
    try:
        flow_url = f"{url}/api/v1/flows/{flow_id}"
        response = httpx.get(flow_url)
        if response.status_code == httpx.codes.OK:
            json_response = response.json()
            return FlowBase(**json_response).model_dump()
    except Exception as e:
        msg = f"Error retrieving flow: {e}"
        raise UploadError(msg) from e


__all__ = ["UploadError", "get_flow", "replace_tweaks_with_env", "upload", "upload_file"]
