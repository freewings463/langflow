"""
模块名称：compression

本模块提供响应数据压缩功能，主要用于减少API响应的数据传输量。
主要功能包括：
- 使用gzip算法压缩JSON数据
- 设置适当的HTTP响应头

设计背景：在API响应数据较大时，压缩可以显著减少网络传输时间
注意事项：压缩级别设置为6，在压缩率和性能之间取得平衡
"""

import gzip
import json
from typing import Any

from fastapi import Response
from fastapi.encoders import jsonable_encoder


def compress_response(data: Any) -> Response:
    """压缩数据并将其作为带有适当头部的FastAPI Response返回。
    
    关键路径（三步）：
    1) 将数据转换为JSON字符串并编码为UTF-8字节
    2) 使用gzip算法压缩数据，压缩级别为6
    3) 创建包含压缩数据和必要头部的Response对象
    
    异常流：无异常处理
    性能瓶颈：大数据集的序列化和压缩
    排障入口：检查响应头部是否包含正确的编码信息
    """
    json_data = json.dumps(jsonable_encoder(data)).encode("utf-8")

    compressed_data = gzip.compress(json_data, compresslevel=6)

    return Response(
        content=compressed_data,
        media_type="application/json",
        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding", "Content-Length": str(len(compressed_data))},
    )
