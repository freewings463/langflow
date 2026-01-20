"""NVIDIA System-Assist 组件（Windows 专用）。

本模块通过 NVIDIA System-Assist 调用 GPU 驱动能力，支持查询与简单指令。
主要功能包括：
- 初始化 Rise Client 并缓存初始化状态
- 发送自然语言指令并返回响应

注意事项：仅支持 Windows 平台，其他平台会抛出错误。
"""

import asyncio

from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.io import MessageTextInput, Output
from lfx.schema import Message
from lfx.services.cache.utils import CacheMiss

RISE_INITIALIZED_KEY = "rise_initialized"


class NvidiaSystemAssistComponent(ComponentWithCache):
    """NVIDIA System-Assist 组件封装。

    契约：输入为自然语言 `prompt`；输出为 `Message`。
    副作用：触发系统级调用并使用共享缓存记录初始化状态。
    失败语义：非 Windows 平台或依赖缺失抛 `ValueError`。
    """

    display_name = "NVIDIA System-Assist"
    description = (
        "(Windows only) Prompts NVIDIA System-Assist to interact with the NVIDIA GPU Driver. "
        "The user may query GPU specifications, state, and ask the NV-API to perform "
        "several GPU-editing acations. The prompt must be human-readable language."
    )
    documentation = "https://docs.langflow.org/bundles-nvidia"
    icon = "NVIDIA"
    rise_initialized = False

    inputs = [
        MessageTextInput(
            name="prompt",
            display_name="System-Assist Prompt",
            info="Enter a prompt for NVIDIA System-Assist to process. Example: 'What is my GPU?'",
            value="",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="sys_assist_prompt"),
    ]

    def maybe_register_rise_client(self):
        """按需初始化 Rise Client 并记录缓存标记。

        副作用：调用系统库注册客户端并写入共享缓存。
        失败语义：平台不支持或初始化失败抛 `ValueError`。
        """
        try:
            from gassist.rise import register_rise_client

            rise_initialized = self._shared_component_cache.get(RISE_INITIALIZED_KEY)
            if not isinstance(rise_initialized, CacheMiss) and rise_initialized:
                return
            self.log("Initializing Rise Client")

            register_rise_client()
            self._shared_component_cache.set(key=RISE_INITIALIZED_KEY, value=True)
        except ImportError as e:
            msg = "NVIDIA System-Assist is Windows only and not supported on this platform"
            raise ValueError(msg) from e
        except Exception as e:
            msg = f"An error occurred initializing NVIDIA System-Assist: {e}"
            raise ValueError(msg) from e

    async def sys_assist_prompt(self) -> Message:
        """发送 System-Assist 指令并返回响应。

        契约：输入为 `prompt`；输出为 `Message(text=...)`。
        失败语义：平台不支持或依赖缺失抛 `ValueError`。
        """
        try:
            from gassist.rise import send_rise_command
        except ImportError as e:
            msg = "NVIDIA System-Assist is Windows only and not supported on this platform"
            raise ValueError(msg) from e

        self.maybe_register_rise_client()

        response = await asyncio.to_thread(send_rise_command, self.prompt)

        return Message(text=response["completed_response"]) if response is not None else Message(text=None)
