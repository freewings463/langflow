"""模块名称：VectorStore 信息组件

本模块封装 LangChain `VectorStoreInfo` 的构建，便于在路由代理中描述向量库能力。
主要功能包括：收集名称/描述/实例并输出 `VectorStoreInfo`。

关键组件：
- `VectorStoreInfoComponent`：向量库描述组件入口

设计背景：向量库路由需要结构化描述以供 LLM 选择。
注意事项：`name/description` 必填，否则路由效果较差。
"""

from langchain.agents.agent_toolkits.vectorstore.toolkit import VectorStoreInfo

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput, MessageTextInput, MultilineInput
from lfx.template.field.base import Output


class VectorStoreInfoComponent(Component):
    """向量库信息组件。

    契约：输入 `vectorstore_name/vectorstore_description/input_vectorstore`；输出 `VectorStoreInfo`；
    副作用：更新 `self.status`；失败语义：输入缺失会由上游校验或运行时异常体现。
    关键路径：1) 写入状态 2) 构建 `VectorStoreInfo` 3) 返回结果。
    决策：状态中只保存名称与描述
    问题：状态应保持轻量便于调试
    方案：不在状态中存储向量库实例
    代价：调试时无法直接查看向量库对象
    重评：当需要更深入排障时增加对象摘要
    """
    display_name = "VectorStoreInfo"
    description = "Information about a VectorStore"
    name = "VectorStoreInfo"
    legacy: bool = True
    icon = "LangChain"

    inputs = [
        MessageTextInput(
            name="vectorstore_name",
            display_name="Name",
            info="Name of the VectorStore",
            required=True,
        ),
        MultilineInput(
            name="vectorstore_description",
            display_name="Description",
            info="Description of the VectorStore",
            required=True,
        ),
        HandleInput(
            name="input_vectorstore",
            display_name="Vector Store",
            input_types=["VectorStore"],
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Vector Store Info", name="info", method="build_info"),
    ]

    def build_info(self) -> VectorStoreInfo:
        """构建 `VectorStoreInfo` 描述对象。

        契约：输入 `vectorstore_name/vectorstore_description/input_vectorstore`；输出 `VectorStoreInfo`；
        副作用：更新 `self.status`；失败语义：无。
        关键路径：1) 写入状态 2) 生成 `VectorStoreInfo`。
        决策：状态保持文本字段
        问题：避免状态序列化时出现复杂对象
        方案：只记录字符串
        代价：缺少实例级调试信息
        重评：当需要更详细信息时加入 `repr` 片段
        """
        self.status = {
            "name": self.vectorstore_name,
            "description": self.vectorstore_description,
        }
        return VectorStoreInfo(
            vectorstore=self.input_vectorstore,
            description=self.vectorstore_description,
            name=self.vectorstore_name,
        )
