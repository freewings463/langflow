"""
模块名称：运行期工具函数

本模块提供运行期的轻量辅助函数。主要功能包括：
- 解析 LangChain 对象的 `memory_key` 映射

关键组件：
- `get_memory_key`：记忆键映射工具

设计背景：不同记忆实现使用不同 key 命名，需要统一映射。
使用场景：运行期兼容不同记忆组件的 key 命名。
注意事项：未匹配到映射时返回 None。
"""


def get_memory_key(langchain_object):
    """从 LangChain 对象中解析互换的 memory_key。

    契约：若对象存在 `memory.memory_key` 则返回映射后的键，否则返回 None。
    副作用：无。
    失败语义：缺少 `memory` 或 `memory_key` 时返回 None。
    决策：使用固定映射表进行兼容。
    问题：不同实现的 key 命名不一致导致上下游不匹配。
    方案：在常见键之间互转。
    代价：未覆盖的新 key 将无法映射。
    重评：当引入新记忆实现时扩展映射表。
    """
    mem_key_dict = {
        "chat_history": "history",
        "history": "chat_history",
    }
    # 注意：仅当对象包含 `memory_key` 时返回映射。
    if hasattr(langchain_object.memory, "memory_key"):
        memory_key = langchain_object.memory.memory_key
        return mem_key_dict.get(memory_key)
    return None
