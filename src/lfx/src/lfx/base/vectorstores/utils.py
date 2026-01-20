"""
模块名称：向量库数据转换工具

本模块提供向量库特定结构到 `Data` 的转换工具，便于统一下游处理。主要功能包括：
- 将 Chroma 集合结构转换为 `Data` 列表

关键组件：
- `chroma_collection_to_data`

设计背景：不同向量库存储结构各异，需要统一成组件层可消费的 `Data`。
使用场景：将 Chroma 查询结果转换为通用数据结构后传递给组件。
注意事项：依赖 `collection_dict` 中 `documents` 与 `ids` 字段存在。
"""

from lfx.schema.data import Data


def chroma_collection_to_data(collection_dict: dict):
    """将 Chroma 集合结构转换为 `Data` 列表

    契约：输入 `collection_dict`（含 `documents`/`ids`/可选 `metadatas`）；输出 `list[Data]`；
    副作用：无；失败语义：缺失关键字段将触发 `KeyError`。
    关键路径：1) 逐文档读取 `documents`/`ids` 2) 合并可用 `metadatas` 3) 构造 `Data`。
    决策：使用 `metadatas` 的键值对直接合并到 `Data` 字段。
    问题：需要保留检索元信息并与文本一起输出。
    方案：在转换阶段合并元数据。
    代价：字段名可能与 `Data` 预定义字段冲突。
    重评：当元数据需命名空间隔离时。
    """
    data = []
    for i, doc in enumerate(collection_dict["documents"]):
        data_dict = {
            "id": collection_dict["ids"][i],
            "text": doc,
        }
        if ("metadatas" in collection_dict) and collection_dict["metadatas"][i]:
            data_dict.update(collection_dict["metadatas"][i].items())
        data.append(Data(**data_dict))
    return data
