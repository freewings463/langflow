"""模块名称：OpenAPI 代理组件

本模块封装 LangChain OpenAPI 工具包，用于基于 OpenAPI 规范生成可调用代理。
主要功能包括：加载 JSON/YAML 规范、构建 `OpenAPIToolkit`、创建代理执行器。

关键组件：
- `OpenAPIAgentComponent`：OpenAPI 代理组件入口

设计背景：让 LLM 能安全地调用结构化 API。
注意事项：`allow_dangerous_requests` 为显式安全开关。
"""

from pathlib import Path

import yaml
from langchain.agents import AgentExecutor
from langchain_community.agent_toolkits import create_openapi_agent
from langchain_community.agent_toolkits.openapi.toolkit import OpenAPIToolkit
from langchain_community.tools.json.tool import JsonSpec
from langchain_community.utilities.requests import TextRequestsWrapper

from lfx.base.agents.agent import LCAgentComponent
from lfx.inputs.inputs import BoolInput, FileInput, HandleInput


class OpenAPIAgentComponent(LCAgentComponent):
    """OpenAPI 代理组件。

    契约：输入 `llm/path/allow_dangerous_requests`；输出 `AgentExecutor`；
    副作用：读取规范文件；失败语义：规范解析错误会抛异常。
    关键路径：1) 解析规范文件 2) 构建 `OpenAPIToolkit` 3) 创建代理。
    决策：使用 `TextRequestsWrapper`
    问题：需要统一的 HTTP 访问封装
    方案：采用 LangChain 提供的请求包装器
    代价：定制能力受限
    重评：当需要更强自定义时允许注入自定义 wrapper
    """
    display_name = "OpenAPI Agent"
    description = "Agent to interact with OpenAPI API."
    name = "OpenAPIAgent"
    icon = "LangChain"
    inputs = [
        *LCAgentComponent.get_base_inputs(),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=True),
        FileInput(name="path", display_name="File Path", file_types=["json", "yaml", "yml"], required=True),
        BoolInput(name="allow_dangerous_requests", display_name="Allow Dangerous Requests", value=False, required=True),
    ]

    def build_agent(self) -> AgentExecutor:
        """构建 OpenAPI 代理执行器。

        契约：输入 `llm/path/allow_dangerous_requests`；输出 `AgentExecutor`；副作用：读取文件；
        失败语义：规范文件不可读或格式错误会抛异常。
        关键路径：1) 解析 YAML/JSON 规范 2) 构建 toolkit 3) 创建代理。
        决策：将 `max_iterations` 提前提升到顶层参数
        问题：OpenAPI agent 的参数结构与其他代理不一致
        方案：把 `agent_executor_kwargs.max_iterations` 上移
        代价：与其他代理调用方式不完全一致
        重评：当上游 API 统一后移除此适配逻辑
        """
        path = Path(self.path)
        if path.suffix in {"yaml", "yml"}:
            with path.open(encoding="utf-8") as file:
                yaml_dict = yaml.safe_load(file)
            spec = JsonSpec(dict_=yaml_dict)
        else:
            spec = JsonSpec.from_file(path)
        requests_wrapper = TextRequestsWrapper()
        toolkit = OpenAPIToolkit.from_llm(
            llm=self.llm,
            json_spec=spec,
            requests_wrapper=requests_wrapper,
            allow_dangerous_requests=self.allow_dangerous_requests,
        )

        agent_args = self.get_agent_kwargs()

        # 注意：OpenAPI agent 的 `max_iterations` 需要在顶层参数传入
        agent_args["max_iterations"] = agent_args["agent_executor_kwargs"]["max_iterations"]
        del agent_args["agent_executor_kwargs"]["max_iterations"]
        return create_openapi_agent(llm=self.llm, toolkit=toolkit, **agent_args)
