"""模块名称：通用常量集合

模块目的：集中维护跨模块复用的常量与枚举值。
主要功能：
- LLM 模型候选列表（用于 UI/配置校验）
- 文档加载器元数据与文件类型映射
- 消息发送方标识与扩展名到 MIME 的映射
使用场景：前端选项渲染、配置校验、内容类型推断。
关键组件：`OPENAI_MODELS`、`LOADERS_INFO`、`EXTENSION_TO_CONTENT_TYPE`
设计背景：将分散的硬编码集中管理，降低组件间耦合。
注意事项：模型列表变化频繁，更新时需同步 UI 选项与兼容性测试。
"""

from typing import Any

OPENAI_MODELS = [
    "text-davinci-003",
    "text-davinci-002",
    "text-curie-001",
    "text-babbage-001",
    "text-ada-001",
]
CHAT_OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo-preview",
    "gpt-4-0125-preview",
    "gpt-4-1106-preview",
    "gpt-4-vision-preview",
    "gpt-3.5-turbo-0125",
    "gpt-3.5-turbo-1106",
]

REASONING_OPENAI_MODELS = [
    "o1",
    "o1-mini",
    "o1-pro",
    "o3-mini",
    "o3",
    "o3-pro",
    "o4-mini",
    "o4-mini-high",
]

ANTHROPIC_MODELS = [
    # 最大模型，适合复杂任务的通用场景
    "claude-v1",
    # `claude-v1` 的扩展版本，提供 100,000 token（约 75,000 词）上下文
    "claude-v1-100k",
    # 低延迟小模型，约 40 词/秒采样
    "claude-instant-v1",
    # `claude-instant-v1` 的 100,000 token 上下文版本
    "claude-instant-v1-100k",
    # 具体子版本
    # 相比 claude-v1.2：更好的指令跟随、代码与非英文对话/写作
    "claude-v1.3",
    # `claude-v1.3` 的 100,000 token 上下文版本
    "claude-v1.3-100k",
    # 相比 claude-v1.1：通用帮助性、指令跟随与编码能力小幅提升
    "claude-v1.2",
    # `claude-v1` 的更早版本
    "claude-v1.0",
    # `claude-instant-v1` 的最新版本
    "claude-instant-v1.1",
    # `claude-instant-v1.1` 的 100,000 token 上下文版本
    "claude-instant-v1.1-100k",
    # `claude-instant-v1` 的更早版本
    "claude-instant-v1.0",
]

DEFAULT_PYTHON_FUNCTION = """
def python_function(text: str) -> str:
    \"\"\"This is a default python function that returns the input text\"\"\"
    return text
"""


PYTHON_BASIC_TYPES = [str, bool, int, float, tuple, list, dict, set]
DIRECT_TYPES = [
    "str",
    "bool",
    "dict",
    "int",
    "float",
    "Any",
    "prompt",
    "mustache",
    "code",
    "NestedDict",
    "table",
    "slider",
    "tab",
    "sortableList",
    "auth",
    "connect",
    "query",
    "tools",
    "mcp",
    "model",
]


LOADERS_INFO: list[dict[str, Any]] = [
    {
        "loader": "AirbyteJSONLoader",
        "name": "Airbyte JSON (.jsonl)",
        "import": "langchain_community.document_loaders.AirbyteJSONLoader",
        "defaultFor": ["jsonl"],
        "allowdTypes": ["jsonl"],
    },
    {
        "loader": "JSONLoader",
        "name": "JSON (.json)",
        "import": "langchain_community.document_loaders.JSONLoader",
        "defaultFor": ["json"],
        "allowdTypes": ["json"],
    },
    {
        "loader": "BSHTMLLoader",
        "name": "BeautifulSoup4 HTML (.html, .htm)",
        "import": "langchain_community.document_loaders.BSHTMLLoader",
        "allowdTypes": ["html", "htm"],
    },
    {
        "loader": "CSVLoader",
        "name": "CSV (.csv)",
        "import": "langchain_community.document_loaders.CSVLoader",
        "defaultFor": ["csv"],
        "allowdTypes": ["csv"],
    },
    {
        "loader": "CoNLLULoader",
        "name": "CoNLL-U (.conllu)",
        "import": "langchain_community.document_loaders.CoNLLULoader",
        "defaultFor": ["conllu"],
        "allowdTypes": ["conllu"],
    },
    {
        "loader": "EverNoteLoader",
        "name": "EverNote (.enex)",
        "import": "langchain_community.document_loaders.EverNoteLoader",
        "defaultFor": ["enex"],
        "allowdTypes": ["enex"],
    },
    {
        "loader": "FacebookChatLoader",
        "name": "Facebook Chat (.json)",
        "import": "langchain_community.document_loaders.FacebookChatLoader",
        "allowdTypes": ["json"],
    },
    {
        "loader": "OutlookMessageLoader",
        "name": "Outlook Message (.msg)",
        "import": "langchain_community.document_loaders.OutlookMessageLoader",
        "defaultFor": ["msg"],
        "allowdTypes": ["msg"],
    },
    {
        "loader": "PyPDFLoader",
        "name": "PyPDF (.pdf)",
        "import": "langchain_community.document_loaders.PyPDFLoader",
        "defaultFor": ["pdf"],
        "allowdTypes": ["pdf"],
    },
    {
        "loader": "STRLoader",
        "name": "Subtitle (.str)",
        "import": "langchain_community.document_loaders.STRLoader",
        "defaultFor": ["str"],
        "allowdTypes": ["str"],
    },
    {
        "loader": "TextLoader",
        "name": "Text (.txt)",
        "import": "langchain_community.document_loaders.TextLoader",
        "defaultFor": ["txt"],
        "allowdTypes": ["txt"],
    },
    {
        "loader": "UnstructuredEmailLoader",
        "name": "Unstructured Email (.eml)",
        "import": "langchain_community.document_loaders.UnstructuredEmailLoader",
        "defaultFor": ["eml"],
        "allowdTypes": ["eml"],
    },
    {
        "loader": "UnstructuredHTMLLoader",
        "name": "Unstructured HTML (.html, .htm)",
        "import": "langchain_community.document_loaders.UnstructuredHTMLLoader",
        "defaultFor": ["html", "htm"],
        "allowdTypes": ["html", "htm"],
    },
    {
        "loader": "UnstructuredMarkdownLoader",
        "name": "Unstructured Markdown (.md)",
        "import": "langchain_community.document_loaders.UnstructuredMarkdownLoader",
        "defaultFor": ["md", "mdx"],
        "allowdTypes": ["md", "mdx"],
    },
    {
        "loader": "UnstructuredPowerPointLoader",
        "name": "Unstructured PowerPoint (.pptx)",
        "import": "langchain_community.document_loaders.UnstructuredPowerPointLoader",
        "defaultFor": ["pptx"],
        "allowdTypes": ["pptx"],
    },
    {
        "loader": "UnstructuredWordLoader",
        "name": "Unstructured Word (.docx)",
        "import": "langchain_community.document_loaders.UnstructuredWordLoader",
        "defaultFor": ["docx"],
        "allowdTypes": ["docx"],
    },
]


MESSAGE_SENDER_AI = "Machine"
MESSAGE_SENDER_USER = "User"
MESSAGE_SENDER_NAME_AI = "AI"
MESSAGE_SENDER_NAME_USER = "User"
EXTENSION_TO_CONTENT_TYPE = {
    "json": "application/json",
    "txt": "text/plain",
    "csv": "text/csv",
    "html": "text/html",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "zip": "application/zip",
    "tar": "application/x-tar",
    "gz": "application/gzip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xml": "application/xml",
    "yaml": "application/x-yaml",
    "yml": "application/x-yaml",
}
