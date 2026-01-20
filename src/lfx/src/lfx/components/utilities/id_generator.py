"""
模块名称：ID 生成组件

本模块提供 UUID 生成能力，主要用于在流程中快速生成唯一标识。主要功能包括：
- 在构建配置时预生成并刷新 ID
- 在执行时返回用户输入或新生成 ID
- 以 `Message` 形式返回结果

关键组件：
- `IDGeneratorComponent`：组件主体
- `update_build_config`：刷新可视化配置中的 ID
- `generate_id`：生成并输出 ID

设计背景：提供轻量、无外部依赖的唯一标识生成能力。
使用场景：为数据行、消息或流程节点生成标识。
注意事项：使用 `UUID4`，无法保证在分布式系统中的绝对唯一性但冲突概率极低。
"""

import uuid
from typing import Any

from typing_extensions import override

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message


class IDGeneratorComponent(Component):
    """UUID 生成组件。

    契约：输出 `Message` 文本形式的 UUID；支持用户输入覆盖。
    副作用：更新 `self.status`，并在配置刷新时生成新 UUID。
    失败语义：标准库异常极少见，若发生将由上层捕获。
    决策：使用 `UUID4` 而非递增序列。
    问题：需要无需外部存储即可生成唯一标识。
    方案：采用随机 UUID4，避免集中式计数器。
    代价：无序且不可读，难以按时间排序。
    重评：当需要可排序 ID 或更短编码时评估替代方案。
    """
    display_name = "ID Generator"
    description = "Generates a unique ID."
    icon = "fingerprint"
    name = "IDGenerator"
    legacy = True

    inputs = [
        MessageTextInput(
            name="unique_id",
            display_name="Value",
            info="The generated unique ID.",
            refresh_button=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="ID", name="id", method="generate_id"),
    ]

    @override
    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """刷新指定字段的默认值为新 UUID。

        契约：仅当 `field_name == "unique_id"` 时更新配置值。
        副作用：修改 `build_config`。
        失败语义：无（仅调用标准库 UUID）。
        决策：在 UI 刷新阶段生成默认值。
        问题：需要用户在界面上看到可用的示例 ID。
        方案：每次刷新时写入新的 UUID4。
        代价：频繁刷新会改变默认值。
        重评：当需要稳定默认值时改为缓存一次。
        """
        if field_name == "unique_id":
            build_config[field_name]["value"] = str(uuid.uuid4())
        return build_config

    def generate_id(self) -> Message:
        """生成或返回已有 ID，并封装为 `Message`。

        契约：若输入为空则生成 UUID4；否则回传输入值。
        副作用：更新 `self.status`。
        决策：优先尊重用户输入。
        问题：上游可能需要固定 ID 进行对齐。
        方案：存在输入则直接返回输入。
        代价：可能传入非 UUID 格式。
        重评：当必须保证 UUID 格式时增加校验。
        """
        unique_id = self.unique_id or str(uuid.uuid4())
        self.status = f"Generated ID: {unique_id}"
        return Message(text=unique_id)
