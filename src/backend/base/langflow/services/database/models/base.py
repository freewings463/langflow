"""
模块名称：模型序列化辅助

本模块提供基于 `orjson` 的序列化封装，统一输出字符串格式。
主要功能包括：支持排序键、缩进与自定义默认序列化函数。

关键组件：`orjson_dumps`
设计背景：统一 `SQLModel` 相关对象的 JSON 输出格式，避免重复配置。
使用场景：模型序列化、调试输出、日志记录。
注意事项：`orjson.dumps` 返回 `bytes`，本函数会统一解码为 `str`。
"""

import orjson


def orjson_dumps(v, *, default=None, sort_keys=False, indent_2=True):
    """封装 `orjson.dumps` 并返回字符串。

    契约：
    - 输入：`v` 为待序列化对象；`default` 为回退序列化函数。
    - 输出：`str` 类型的 JSON 字符串。
    - 副作用：无。
    - 失败语义：序列化失败时抛出 `orjson.JSONEncodeError`。

    关键路径：
    1) 依据 `sort_keys` 与 `indent_2` 构造 `option`。
    2) 调用 `orjson.dumps` 序列化为 `bytes`。
    3) 统一解码为 `str` 返回。

    决策：统一返回 `str` 而非 `bytes`。
    问题：调用方期望与标准 `json.dumps` 一致的返回类型。
    方案：在封装中执行 `decode()`。
    代价：多一次内存拷贝。
    重评：当调用方可接受 `bytes` 时提供可选开关。
    """
    option = orjson.OPT_SORT_KEYS if sort_keys else None
    if indent_2:
        # 注意：`orjson.dumps` 返回 `bytes`，需解码以对齐 `json.dumps` 行为。
        # 注意：`option` 可通过按位或组合多个常量。
        if option is None:
            option = orjson.OPT_INDENT_2
        else:
            option |= orjson.OPT_INDENT_2
    if default is None:
        return orjson.dumps(v, option=option).decode()
    return orjson.dumps(v, default=default, option=option).decode()
