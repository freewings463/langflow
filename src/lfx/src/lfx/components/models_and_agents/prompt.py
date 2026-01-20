"""
模块名称：Prompt 模板组件

本模块提供 Prompt 模板解析与变量提取能力，支持 f-string 与 mustache 双语法模式。
主要功能：
- 解析模板并动态生成输入字段；
- 校验 mustache 模板安全性；
- 根据模板与变量生成最终 Prompt。

关键组件：
- PromptComponent：模板组件入口。

设计背景：兼容不同模板语法并保障模板安全。
注意事项：切换语法会重建变量字段，旧字段可能被清理。
"""

from typing import Any

from lfx.base.prompts.api_utils import process_prompt_template
from lfx.custom.custom_component.component import Component
from lfx.inputs.input_mixin import FieldTypes
from lfx.inputs.inputs import DefaultPromptField
from lfx.io import BoolInput, MessageTextInput, Output, PromptInput
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message
from lfx.template.utils import update_template_values
from lfx.utils.mustache_security import validate_mustache_template


class PromptComponent(Component):
    """Prompt 模板组件封装

    契约：输入模板与变量字段，输出 `Message`；支持 f-string 与 mustache 模式。
    关键路径：1) 解析模板字段 2) 校验 mustache 安全 3) 生成最终 Prompt。
    决策：提供语法切换而非强制单一模板格式
    问题：不同用户/历史流程使用不同模板语法
    方案：通过 `use_double_brackets` 切换解析器
    代价：切换时会清理旧模式字段
    重评：当模板语法统一或迁移完成时
    """

    display_name: str = "Prompt Template"
    description: str = "Create a prompt template with dynamic variables."
    documentation: str = "https://docs.langflow.org/components-prompts"
    icon = "prompts"
    trace_type = "prompt"
    name = "Prompt Template"
    priority = 0  # 注意：优先级置 0 以在列表中靠前展示。

    inputs = [
        PromptInput(name="template", display_name="Template"),
        BoolInput(
            name="use_double_brackets",
            display_name="Use Double Brackets",
            value=False,
            advanced=True,
            info="Use {{variable}} syntax instead of {variable}.",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="tool_placeholder",
            display_name="Tool Placeholder",
            tool_mode=True,
            advanced=True,
            info="A placeholder input for tool mode.",
        ),
    ]

    outputs = [
        Output(display_name="Prompt", name="prompt", method="build_prompt"),
    ]

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None) -> dotdict:
        """根据语法模式更新模板字段类型

        契约：修改 `build_config` 并返回；副作用：清理旧变量字段并重建。
        关键路径：1) 切换字段类型 2) 清理旧字段 3) 重新解析模板。
        异常流：模板校验失败时保留模式切换并记录日志。
        排障入口：日志 `Template validation failed during mode switch`。
        决策：切换语法时重建字段而非增量修改
        问题：旧语法字段会污染新语法解析
        方案：先清理再重建
        代价：可能丢失旧字段值
        重评：当支持字段迁移映射时
        """
        if field_name == "use_double_brackets":
            # 注意：根据语法模式切换模板字段类型。
            is_mustache = field_value is True
            if is_mustache:
                build_config["template"]["type"] = FieldTypes.MUSTACHE_PROMPT.value
            else:
                build_config["template"]["type"] = FieldTypes.PROMPT.value

            # 实现：模式切换后重新解析变量字段。
            template_value = build_config.get("template", {}).get("value", "")
            if template_value:
                # 注意：确保 custom_fields 已初始化。
                if "custom_fields" not in build_config:
                    build_config["custom_fields"] = {}

                # 注意：先清理旧语法字段，避免校验失败后遗留错误字段。
                old_custom_fields = build_config["custom_fields"].get("template", [])
                for old_field in list(old_custom_fields):
                    # 实现：从 custom_fields 与模板配置中移除旧字段。
                    if old_field in old_custom_fields:
                        old_custom_fields.remove(old_field)
                    build_config.pop(old_field, None)

                # 注意：即使校验失败，也至少完成旧字段清理。
                try:
                    # 安全：mustache 模板需先做安全校验。
                    if is_mustache:
                        validate_mustache_template(template_value)

                    # 实现：按新语法重建变量字段。
                    _ = process_prompt_template(
                        template=template_value,
                        name="template",
                        custom_fields=build_config["custom_fields"],
                        frontend_node_template=build_config,
                        is_mustache=is_mustache,
                    )
                except ValueError as e:
                # 注意：校验失败时仅记录日志，保存时再提示用户。
                    logger.debug(f"Template validation failed during mode switch: {e}")
        return build_config

    async def build_prompt(self) -> Message:
        """基于模板与变量生成 Prompt 消息

        契约：返回 `Message`，文本由模板与变量渲染得到。
        关键路径：1) 判断语法模式 2) 调用模板渲染。
        决策：通过 `Message.from_template_and_variables` 统一渲染
        问题：多语法解析容易产生不一致
        方案：交由 Message 统一处理
        代价：对 Message 渲染实现存在耦合
        重评：当渲染逻辑下沉到独立服务时
        """
        use_double_brackets = self.use_double_brackets if hasattr(self, "use_double_brackets") else False
        template_format = "mustache" if use_double_brackets else "f-string"
        prompt = await Message.from_template_and_variables(template_format=template_format, **self._attributes)
        self.status = prompt.text
        return prompt

    def _update_template(self, frontend_node: dict):
        """更新前端模板并提取变量字段

        契约：更新 `frontend_node` 并返回；失败时保留原结构。
        关键路径：1) 读取模板 2) 校验 mustache 3) 解析并填充字段。
        异常流：校验失败仅记录日志，不阻断组件创建。
        """
        prompt_template = frontend_node["template"]["template"]["value"]
        use_double_brackets = frontend_node["template"].get("use_double_brackets", {}).get("value", False)
        is_mustache = use_double_brackets is True

        try:
            # 安全：mustache 模板需先做安全校验。
            if is_mustache:
                validate_mustache_template(prompt_template)

            custom_fields = frontend_node["custom_fields"]
            frontend_node_template = frontend_node["template"]
            _ = process_prompt_template(
                template=prompt_template,
                name="template",
                custom_fields=custom_fields,
                frontend_node_template=frontend_node_template,
                is_mustache=is_mustache,
            )
        except ValueError as e:
            # 注意：校验失败时不添加变量，但允许组件创建。
            logger.debug(f"Template validation failed in _update_template: {e}")
        return frontend_node

    async def update_frontend_node(self, new_frontend_node: dict, current_frontend_node: dict):
        """更新前端节点并回填模板字段值

        契约：返回更新后的前端节点；副作用：模板字段与变量值同步。
        关键路径：1) 校验模板 2) 解析变量字段 3) 回填旧值。
        异常流：模板校验失败时仅记录日志，组件仍可更新。
        决策：回填旧值以保留用户输入
        问题：模板变更会丢失已填变量
        方案：对比新旧模板并回填
        代价：字段名冲突时可能覆盖
        重评：当前端支持字段版本管理时
        """
        frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        template = frontend_node["template"]["template"]["value"]
        use_double_brackets = frontend_node["template"].get("use_double_brackets", {}).get("value", False)
        is_mustache = use_double_brackets is True

        try:
            # 安全：mustache 模板需先做安全校验。
            if is_mustache:
                validate_mustache_template(template)

            # 注意：为兼容旧逻辑保留重复解析。
            _ = process_prompt_template(
                template=template,
                name="template",
                custom_fields=frontend_node["custom_fields"],
                frontend_node_template=frontend_node["template"],
                is_mustache=is_mustache,
            )
        except ValueError as e:
            # 注意：校验失败时不添加变量，但允许组件更新。
            logger.debug(f"Template validation failed in update_frontend_node: {e}")
        # 实现：模板更新后回填旧节点上的变量值。
        update_template_values(new_template=frontend_node, previous_template=current_frontend_node["template"])
        return frontend_node

    def _get_fallback_input(self, **kwargs):
        """提供模板变量的默认输入字段类型。"""
        return DefaultPromptField(**kwargs)
