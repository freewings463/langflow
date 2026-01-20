"""
模块名称：baidu_qianfan_chat

本模块提供百度千帆聊天模型组件封装，基于 LangChain 的 QianfanChatEndpoint。
主要功能包括：
- 构建并返回千帆聊天模型实例
- 暴露常见模型与参数选项供界面配置

关键组件：
- `QianfanChatEndpointComponent`：千帆聊天模型组件

设计背景：需要在 Langflow 中以统一接口接入百度千帆
使用场景：在流程中选择千帆模型作为对话模型
注意事项：AK/SK 与自定义 endpoint 需由调用方提供
"""

from langchain_community.chat_models.baidu_qianfan_endpoint import QianfanChatEndpoint

from lfx.base.models.model import LCModelComponent
from lfx.field_typing.constants import LanguageModel
from lfx.io import DropdownInput, FloatInput, MessageTextInput, SecretStrInput


class QianfanChatEndpointComponent(LCModelComponent):
    """百度千帆聊天模型组件。

    契约：需提供 `qianfan_ak/qianfan_sk`，并选择模型名。
    副作用：创建 LangChain Qianfan 客户端实例。
    失败语义：初始化失败抛 `ValueError`，上层需提示用户配置问题。
    """
    display_name: str = "Qianfan"
    description: str = "Generate text using Baidu Qianfan LLMs."
    documentation: str = "https://python.langchain.com/docs/integrations/chat/baidu_qianfan_endpoint"
    icon = "BaiduQianfan"
    name = "BaiduQianfanChatModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        DropdownInput(
            name="model",
            display_name="Model Name",
            options=[
                "EB-turbo-AppBuilder",
                "Llama-2-70b-chat",
                "ERNIE-Bot-turbo-AI",
                "ERNIE-Lite-8K-0308",
                "ERNIE-Speed",
                "Qianfan-Chinese-Llama-2-13B",
                "ERNIE-3.5-8K",
                "BLOOMZ-7B",
                "Qianfan-Chinese-Llama-2-7B",
                "XuanYuan-70B-Chat-4bit",
                "AquilaChat-7B",
                "ERNIE-Bot-4",
                "Llama-2-13b-chat",
                "ChatGLM2-6B-32K",
                "ERNIE-Bot",
                "ERNIE-Speed-128k",
                "ERNIE-4.0-8K",
                "Qianfan-BLOOMZ-7B-compressed",
                "ERNIE Speed",
                "Llama-2-7b-chat",
                "Mixtral-8x7B-Instruct",
                "ERNIE 3.5",
                "ERNIE Speed-AppBuilder",
                "ERNIE-Speed-8K",
                "Yi-34B-Chat",
            ],
            info="https://python.langchain.com/docs/integrations/chat/baidu_qianfan_endpoint",
            value="ERNIE-4.0-8K",
        ),
        SecretStrInput(
            name="qianfan_ak",
            display_name="Qianfan Ak",
            info="which you could get from  https://cloud.baidu.com/product/wenxinworkshop",
        ),
        SecretStrInput(
            name="qianfan_sk",
            display_name="Qianfan Sk",
            info="which you could get from  https://cloud.baidu.com/product/wenxinworkshop",
        ),
        FloatInput(
            name="top_p",
            display_name="Top p",
            info="Model params, only supported in ERNIE-Bot and ERNIE-Bot-turbo",
            value=0.8,
            advanced=True,
        ),
        FloatInput(
            name="temperature",
            display_name="Temperature",
            info="Model params, only supported in ERNIE-Bot and ERNIE-Bot-turbo",
            value=0.95,
        ),
        FloatInput(
            name="penalty_score",
            display_name="Penalty Score",
            info="Model params, only supported in ERNIE-Bot and ERNIE-Bot-turbo",
            value=1.0,
            advanced=True,
        ),
        MessageTextInput(
            name="endpoint", display_name="Endpoint", info="Endpoint of the Qianfan LLM, required if custom model used."
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建并返回千帆模型实例。

        契约：`model` 必须为支持列表；AK/SK 可为空但可能导致认证失败。
        副作用：实例化 `QianfanChatEndpoint` 并进行参数校验。
        失败语义：初始化异常转为 `ValueError`。
        关键路径（三步）：1) 读取输入参数 2) 组装 kwargs 3) 构建模型实例。
        决策：仅在 `endpoint` 非空时写入参数。
        问题：空 endpoint 会被误认为自定义模型并导致错误请求。
        方案：对空值不传递该字段。
        代价：无法区分“用户明确想清空 endpoint”与“未设置”。
        重评：当上游支持显式清空语义时。
        """
        model = self.model
        qianfan_ak = self.qianfan_ak
        qianfan_sk = self.qianfan_sk
        top_p = self.top_p
        temperature = self.temperature
        penalty_score = self.penalty_score
        endpoint = self.endpoint

        try:
            kwargs = {
                "model": model,
                "qianfan_ak": qianfan_ak or None,
                "qianfan_sk": qianfan_sk or None,
                "top_p": top_p,
                "temperature": temperature,
                "penalty_score": penalty_score,
            }

            if endpoint:  # Only add endpoint if it has a value
                kwargs["endpoint"] = endpoint

            output = QianfanChatEndpoint(**kwargs)

        except Exception as e:
            msg = "Could not connect to Baidu Qianfan API."
            raise ValueError(msg) from e

        return output
