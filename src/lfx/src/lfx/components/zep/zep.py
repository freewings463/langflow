"""
模块名称：Zep 聊天记忆组件

本模块提供 Zep 记忆库的接入能力，主要用于将聊天历史存取到 Zep 服务。主要功能包括：
- 组装 Zep 连接参数与会话信息
- 返回 LangChain 兼容的消息历史对象
- 兼容不同 Zep API 版本的 base path 配置

关键组件：
- `ZepChatMemory`：组件主体
- `build_message_history`：创建 Zep 消息历史实例

设计背景：提供可选的外部记忆存储，便于多轮对话持久化。
使用场景：需要跨会话持久化消息或与 Zep 生态集成时。
注意事项：依赖 `zep-python`；API base path 需与服务版本匹配，否则可能 404。
"""

from lfx.base.memory.model import LCChatMemoryComponent
from lfx.field_typing.constants import Memory
from lfx.inputs.inputs import DropdownInput, MessageTextInput, SecretStrInput


class ZepChatMemory(LCChatMemoryComponent):
    """Zep 聊天记忆组件。

    契约：输入 `url`/`api_key`/`api_base_path`/`session_id`；输出 `Memory` 类型的消息历史。
    副作用：设置 `zep_python` 的全局 `API_BASE_PATH`；与外部服务建立连接。
    失败语义：缺少依赖抛 `ImportError`；连接/请求异常由 SDK 抛出。
    关键路径：1) 导入并配置 SDK 2) 初始化客户端 3) 构建消息历史实例。
    决策：通过修改 SDK 全局变量兼容不同 API 版本。
    问题：本地 Zep 实例 API 版本可能与云端不一致，导致 404。
    方案：允许组件输入 `api_base_path` 并写入 `zep_python.zep_client.API_BASE_PATH`。
    代价：影响进程内所有 Zep 客户端的默认路径。
    重评：当 SDK 提供实例级配置或多版本兼容时移除该写法。
    """
    display_name = "Zep Chat Memory"
    description = "Retrieves and store chat messages from Zep."
    name = "ZepChatMemory"
    icon = "ZepMemory"
    legacy = True
    replacement = ["helpers.Memory"]

    inputs = [
        MessageTextInput(name="url", display_name="Zep URL", info="URL of the Zep instance."),
        SecretStrInput(name="api_key", display_name="Zep API Key", info="API Key for the Zep instance."),
        DropdownInput(
            name="api_base_path",
            display_name="API Base Path",
            options=["api/v1", "api/v2"],
            value="api/v1",
            advanced=True,
        ),
        MessageTextInput(
            name="session_id", display_name="Session ID", info="Session ID for the message.", advanced=True
        ),
    ]

    def build_message_history(self) -> Memory:
        """构建并返回 Zep 消息历史对象。

        契约：使用 `session_id` 绑定会话；返回 `ZepChatMessageHistory`。
        副作用：修改 `zep_python.zep_client.API_BASE_PATH` 全局配置。
        失败语义：SDK 导入失败抛 `ImportError`；初始化失败由 SDK 抛出。
        """
        try:
            # 决策：通过全局 `API_BASE_PATH` 适配不同 Zep 部署版本，避免 404。
            import zep_python.zep_client
            from zep_python import ZepClient
            from zep_python.langchain import ZepChatMessageHistory

            zep_python.zep_client.API_BASE_PATH = self.api_base_path
        except ImportError as e:
            msg = "Could not import zep-python package. Please install it with `pip install zep-python`."
            raise ImportError(msg) from e

        zep_client = ZepClient(api_url=self.url, api_key=self.api_key)
        return ZepChatMessageHistory(session_id=self.session_id, zep_client=zep_client)
