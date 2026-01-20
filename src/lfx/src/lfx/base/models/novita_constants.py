"""
模块名称：Novita 模型列表

本模块提供 Novita 平台的模型名称列表，主要用于 UI 展示与模型选择的默认数据源。
主要功能包括：
- 提供 Novita 可用模型名的静态集合
- 维持旧变量名的兼容别名

关键组件：
- `NOVITA_MODELS`：模型名列表

设计背景：在缺少动态发现能力时提供稳定模型列表。
注意事项：实际可用模型以平台为准。
"""

NOVITA_MODELS = [
    "deepseek/deepseek-r1",
    "deepseek/deepseek_v3",
    "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "meta-llama/llama-3.1-70b-instruct",
    "mistralai/mistral-nemo",
    "Sao10K/L3-8B-Stheno-v3.2",
    "gryphe/mythomax-l2-13b",
    "qwen/qwen-2.5-72b-instruct",
    "meta-llama/llama-3-8b-instruct",
    "microsoft/wizardlm-2-8x22b",
    "google/gemma-2-9b-it",
    "mistralai/mistral-7b-instruct",
    "meta-llama/llama-3-70b-instruct",
    "openchat/openchat-7b",
    "nousresearch/hermes-2-pro-llama-3-8b",
    "sao10k/l3-70b-euryale-v2.1",
    "cognitivecomputations/dolphin-mixtral-8x22b",
    "jondurbin/airoboros-l2-70b",
    "nousresearch/nous-hermes-llama2-13b",
    "teknium/openhermes-2.5-mistral-7b",
    "sophosympatheia/midnight-rose-70b",
    "meta-llama/llama-3.1-8b-instruct-max",
    "sao10k/l3-8b-lunaris",
    "qwen/qwen-2-vl-72b-instruct",
    "meta-llama/llama-3.2-1b-instruct",
    "meta-llama/llama-3.2-11b-vision-instruct",
    "meta-llama/llama-3.2-3b-instruct",
    "meta-llama/llama-3.1-8b-instruct-bf16",
    "sao10k/l31-70b-euryale-v2.2",
    "qwen/qwen-2-7b-instruct",
    "qwen/qwen-2-72b-instruct",
]
# 向后兼容别名
MODEL_NAMES = NOVITA_MODELS
