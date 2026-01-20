"""
模块名称：Session 工具函数

本模块提供会话 ID 生成与图数据哈希计算工具。
主要功能：
- 生成短会话 ID
- 过滤并哈希图数据用于缓存键
设计背景：为 SessionService 提供稳定且可复用的辅助方法。
注意事项：哈希输入会先过滤为可序列化 JSON。
"""

import hashlib
import random
import string

from langflow.services.cache.utils import filter_json
from langflow.services.database.models.base import orjson_dumps


def session_id_generator(size=6):
    """生成随机会话 ID（默认长度 6）。"""
    return "".join(random.SystemRandom().choices(string.ascii_uppercase + string.digits, k=size))


def compute_dict_hash(graph_data):
    """对流程字典做稳定哈希，用于缓存键。"""
    graph_data = filter_json(graph_data)

    cleaned_graph_json = orjson_dumps(graph_data, sort_keys=True)

    return hashlib.sha256(cleaned_graph_json.encode("utf-8")).hexdigest()
