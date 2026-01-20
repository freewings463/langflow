"""
模块名称：基础记忆组件抽象

本模块提供 LFX 记忆组件的抽象基类，主要用于定义会话消息的读取与写入接口。
主要功能包括：
- 提供记忆组件的基础配置项与 UI 展示元数据
- 约束消息查询与写入的契约方法，交由子类实现存储细节

关键组件：
- `BaseMemoryComponent`：记忆组件抽象基类

设计背景：将记忆组件的交互契约与具体存储实现解耦，便于切换后端与扩展功能。
注意事项：子类需实现 `get_messages` 与 `add_message`，否则运行时会触发未实现错误。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.schema.data import Data
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_USER


class BaseMemoryComponent(CustomComponent):
    """记忆组件抽象基类。

    契约：`get_messages` 返回 `list[Data]`；`add_message` 写入单条消息且不返回结果。
    副作用：子类通常会进行外部存储 I/O（数据库/缓存/文件）。
    失败语义：存储访问异常由子类抛出并交由上层处理。
    决策：将存储实现隔离在子类中
    问题：记忆组件需要支持多种存储后端
    方案：基类仅定义接口与通用配置，存储细节下沉到实现类
    代价：子类需要重复实现校验与错误处理
    重评：当存储后端趋于统一时评估提取通用实现
    """

    display_name = "Chat Memory"
    description = "Retrieves stored chat messages given a specific Session ID."
    beta: bool = True
    icon = "history"

    def build_config(self):
        """生成记忆组件的配置声明。

        契约：返回用于前端表单渲染的配置字典，包含字段展示名与选项。
        副作用：无。
        失败语义：返回结构错误会导致前端配置渲染失败。
        关键路径（三步）：
        1) 声明发送者与发送者名称字段
        2) 声明消息数量、会话标识与排序字段
        3) 声明数据模板字段并返回配置
        异常流：字段配置缺失会在前端渲染阶段暴露。
        性能瓶颈：无显著开销，仅构造静态字典。
        排障入口：比对 UI 字段名与此处 `display_name` 是否一致。
        决策：在基类集中定义通用配置
        问题：记忆组件在不同后端之间仍需统一 UI 配置
        方案：由基类提供固定配置模板
        代价：子类自定义字段需要扩展或覆盖该方法
        重评：当配置差异显著时拆分为可组合的配置片段
        """

        return {
            "sender": {
                "options": [MESSAGE_SENDER_AI, MESSAGE_SENDER_USER, "Machine and User"],
                "display_name": "Sender Type",
            },
            "sender_name": {"display_name": "Sender Name", "advanced": True},
            "n_messages": {
                "display_name": "Number of Messages",
                "info": "Number of messages to retrieve.",
            },
            "session_id": {
                "display_name": "Session ID",
                "info": "Session ID of the chat history.",
                "input_types": ["Message"],
            },
            "order": {
                "options": ["Ascending", "Descending"],
                "display_name": "Order",
                "info": "Order of the messages.",
                "advanced": True,
            },
            "data_template": {
                "display_name": "Data Template",
                "multiline": True,
                "info": "Template to convert Data to Text. "
                "If left empty, it will be dynamically set to the Data's text key.",
                "advanced": True,
            },
        }

    def get_messages(self, **kwargs) -> list[Data]:
        """读取指定会话的消息列表。

        契约：返回 `list[Data]`，每条消息需包含文本与必要元数据。
        副作用：可能触发外部存储读取。
        失败语义：查询失败时应抛出具体异常，调用方可选择重试或降级为空列表。
        决策：以抽象方法暴露查询能力
        问题：不同存储后端的查询参数与分页能力不一致
        方案：使用 `**kwargs` 由子类定义支持字段
        代价：调用方需了解具体实现支持的参数
        重评：当参数集合稳定时考虑显式类型化
        """

        raise NotImplementedError

    def add_message(
        self, sender: str, sender_name: str, text: str, session_id: str, metadata: dict | None = None, **kwargs
    ) -> None:
        """写入一条消息到指定会话。

        契约：必须持久化 `sender`/`text`/`session_id`，`metadata` 可为空。
        副作用：写入外部存储并可能触发索引/缓存更新。
        失败语义：写入失败应抛出异常；调用方可选择重试或记录告警。
        决策：采用抽象方法以支持多后端写入策略
        问题：不同后端对字段/索引/一致性要求不同
        方案：基类只定义必需字段，其余由 `**kwargs` 扩展
        代价：通用校验难以下沉到基类
        重评：当字段规范与一致性策略统一时再上移实现
        """

        raise NotImplementedError
