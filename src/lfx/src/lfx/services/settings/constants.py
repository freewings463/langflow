"""
模块名称：settings.constants

本模块集中定义设置系统的默认值与环境变量白名单，用于统一配置行为与迁移兼容。
主要功能包括：
- 默认超级用户与初始密码的占位值
- 允许从环境变量自动注入的配置项列表
- Agentic 体验专用的变量集合

关键组件：
- DEFAULT_SUPERUSER / DEFAULT_SUPERUSER_PASSWORD：默认凭据占位
- VARIABLES_TO_GET_FROM_ENVIRONMENT：环境变量白名单
- AGENTIC_VARIABLES：Agentic 功能依赖变量

设计背景：配置来源多且分散，需在单点维护白名单以避免遗漏与不一致。
注意事项：默认凭据仅用于开发/初始化场景，生产环境应显式覆盖。
"""

from pydantic import SecretStr

DEFAULT_SUPERUSER = "langflow"
DEFAULT_SUPERUSER_PASSWORD = SecretStr("langflow")

VARIABLES_TO_GET_FROM_ENVIRONMENT = [
    "COMPOSIO_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_API_INSTANCE_NAME",
    "AZURE_OPENAI_API_DEPLOYMENT_NAME",
    "AZURE_OPENAI_API_EMBEDDINGS_DEPLOYMENT_NAME",
    "ASTRA_DB_APPLICATION_TOKEN",
    "ASTRA_DB_API_ENDPOINT",
    "COHERE_API_KEY",
    "GROQ_API_KEY",
    "HUGGINGFACEHUB_API_TOKEN",
    "PINECONE_API_KEY",
    "SAMBANOVA_API_KEY",
    "SEARCHAPI_API_KEY",
    "SERPAPI_API_KEY",
    "UPSTASH_VECTOR_REST_URL",
    "UPSTASH_VECTOR_REST_TOKEN",
    "VECTARA_CUSTOMER_ID",
    "VECTARA_CORPUS_ID",
    "VECTARA_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "NOVITA_API_KEY",
    "TAVILY_API_KEY",
    "COMETAPI_KEY",
]

# 注意：Agentic 体验专用变量，仅在对应功能启用时注入
AGENTIC_VARIABLES = [
    "FLOW_ID",
    "COMPONENT_ID",
    "FIELD_NAME",
    "ASTRA_TOKEN",
]

DEFAULT_AGENTIC_VARIABLE_VALUE = ""
