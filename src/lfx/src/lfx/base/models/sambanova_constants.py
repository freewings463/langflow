"""
模块名称：SambaNova 模型列表

本模块提供 SambaNova 可用模型名称列表，主要用于 UI 选择与兼容支持。
主要功能包括：
- 提供模型名的静态集合
- 保留旧变量名的兼容别名

关键组件：
- `SAMBANOVA_MODEL_NAMES`：模型名列表

设计背景：在缺少动态发现能力时提供稳定模型列表。
注意事项：实际可用模型以平台为准。
"""

SAMBANOVA_MODEL_NAMES = [
    "Meta-Llama-3.3-70B-Instruct",
    "Meta-Llama-3.1-8B-Instruct",
    "Meta-Llama-3.1-70B-Instruct",
    "Meta-Llama-3.1-405B-Instruct",
    "DeepSeek-R1-Distill-Llama-70B",
    "DeepSeek-R1",
    "Meta-Llama-3.2-1B-Instruct",
    "Meta-Llama-3.2-3B-Instruct",
    "Llama-3.2-11B-Vision-Instruct",
    "Llama-3.2-90B-Vision-Instruct",
    "Qwen2.5-Coder-32B-Instruct",
    "Qwen2.5-72B-Instruct",
    "QwQ-32B-Preview",
    "Qwen2-Audio-7B-Instruct",
]

# 向后兼容别名
MODEL_NAMES = SAMBANOVA_MODEL_NAMES
