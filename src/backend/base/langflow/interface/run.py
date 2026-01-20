"""
模块名称：运行时内存键处理

本模块提供 `LangChain` 运行时 `memory` 键的兼容处理工具，主要用于对接不同版本的 `memory` 字段命名。主要功能包括：
- 推断当前 `memory` 键的互斥键名
- 更新输入/输出/内存键以避免冲突

关键组件：
- `get_memory_key`：获取互斥键名
- `update_memory_keys`：更新 `memory` 相关键

设计背景：历史版本存在 `chat_history`/`history` 命名差异
注意事项：仅在对象包含 `memory` 属性时有效
"""

from lfx.log.logger import logger


def get_memory_key(langchain_object):
    """推断互斥的 `memory` 键名。

    契约：输入包含 `memory` 的对象；输出互斥键名或 `None`。
    关键路径：读取 `memory.memory_key` 并在已知映射中查找。
    失败语义：对象缺失 `memory_key` 时返回 `None`。
    决策：仅识别 `chat_history`/`history` 互斥关系
    问题：历史命名差异导致字段冲突
    方案：固定映射表进行互换
    代价：无法自动处理新的命名约定
    重评：当引入新的 `memory` 键规范时
    """
    mem_key_dict = {
        "chat_history": "history",
        "history": "chat_history",
    }
    if hasattr(langchain_object.memory, "memory_key"):
        memory_key = langchain_object.memory.memory_key
        return mem_key_dict.get(memory_key)
    return None


def update_memory_keys(langchain_object, possible_new_mem_key) -> None:
    """更新 `LangChain` 对象的 `memory` 相关键。

    契约：输入对象与候选 `memory` 键；原地更新对象；无返回值。
    关键路径：1) 计算新的 `input_key`/`output_key` 2) 更新 `memory` 的三个属性。
    失败语义：对象缺少属性时记录 `debug` 日志并跳过该属性。
    注意：`input_keys`/`output_keys` 为空会触发 `StopIteration`。
    """
    input_key = next(
        key
        for key in langchain_object.input_keys
        if key not in {langchain_object.memory.memory_key, possible_new_mem_key}
    )

    output_key = next(
        key
        for key in langchain_object.output_keys
        if key not in {langchain_object.memory.memory_key, possible_new_mem_key}
    )

    for key, attr in [(input_key, "input_key"), (output_key, "output_key"), (possible_new_mem_key, "memory_key")]:
        try:
            setattr(langchain_object.memory, attr, key)
        except ValueError as exc:
            logger.debug(f"{langchain_object.memory} has no attribute {attr} ({exc})")
