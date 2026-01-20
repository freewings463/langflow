"""模块名称：前端节点默认常量

本模块集中存放前端节点模板所需的默认字段与提示词常量。
主要功能包括：
- 前端强制展示字段清单
- 组件默认 Prompt 文案与 QA 链配置
- 模型默认推理参数与 API Base 说明

设计背景：避免在多个组件内重复维护相同默认值，统一变更入口。
注意事项：这些常量会影响前端默认行为，修改需同步更新文案与兼容说明。
"""

# 契约：这些字段即使处于高级模式也必须展示，避免关键配置被隐藏。
FORCE_SHOW_FIELDS = [
    "allowed_tools",
    "memory",
    "prefix",
    "examples",
    "temperature",
    "model_name",
    "headers",
    "max_value_length",
    "max_tokens",
    "google_cse_id",
]

# 默认提示词模板：无自定义时提供基础示例，便于快速试用。
DEFAULT_PROMPT = """
I want you to act as a naming consultant for new companies.

Here are some examples of good company names:

- search engine, Google
- social media, Facebook
- video sharing, YouTube

The name should be short, catchy and easy to remember.

What is a good name for a company that makes {product}?
"""

# 默认系统提示词：保持通用、低风险的对话基调。
SYSTEM_PROMPT = """
You are a helpful assistant that talks casually about life in general.
You are a good listener and you can talk about anything.
"""

HUMAN_PROMPT = "{input}"

# 支持的 QA Chain 类型；与前端选择项保持一致。
QA_CHAIN_TYPES = ["stuff", "map_reduce", "map_rerank", "refine"]

# ctransformers 默认推理参数；保持与前端默认值一致。
CTRANSFORMERS_DEFAULT_CONFIG = {
    "top_k": 40,
    "top_p": 0.95,
    "temperature": 0.8,
    "repetition_penalty": 1.1,
    "last_n_tokens": 64,
    "seed": -1,
    "max_new_tokens": 256,
    "stop": None,
    "stream": False,
    "reset": True,
    "batch_size": 8,
    "threads": -1,
    "context_length": -1,
    "gpu_layers": 0,
}

# 说明：前端需展示可替换的 API Base，以提示用户可接入其他兼容服务。
OPENAI_API_BASE_INFO = """
The base URL of the OpenAI API. Defaults to https://api.openai.com/v1.

You can change this to use other APIs like JinaChat, LocalAI and Prem.
"""


INPUT_KEY_INFO = """The variable to be used as Chat Input when more than one variable is available."""
OUTPUT_KEY_INFO = """The variable to be used as Chat Output (e.g. answer in a ConversationalRetrievalChain)"""
