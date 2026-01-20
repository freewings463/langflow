"""模块名称：LangChain Hub Prompt 组件

本模块提供 LangChain Hub 提示词的拉取与参数化能力，支持根据模板动态生成输入字段。
主要功能包括：拉取 Hub 模板、解析占位符、生成动态输入、构建最终 `Message`。

关键组件：
- `LangChainHubPromptComponent`：Hub Prompt 的组件化入口

设计背景：在可视化流程中直接使用 Hub 模板并自动生成参数输入。
注意事项：必须提供 `langchain_api_key`，模板占位符以 `{name}` 识别。
"""

import re

from langchain_core.prompts import HumanMessagePromptTemplate

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DefaultPromptField, SecretStrInput, StrInput
from lfx.io import Output
from lfx.schema.message import Message


class LangChainHubPromptComponent(Component):
    """LangChain Hub Prompt 组件。

    契约：输入 `langchain_api_key/langchain_hub_prompt/param_*`；输出 `Message`；
    副作用：更新 `build_config` 与 `self.status`；失败语义：缺少 API Key 抛 `ValueError`。
    关键路径：1) 拉取模板 2) 解析占位符并生成输入 3) 构建模板消息。
    决策：以 `{}` 占位符为参数来源
    问题：Hub 模板需要自动生成输入字段
    方案：正则提取 `{...}` 并动态注册参数
    代价：无法区分转义花括号
    重评：当 Hub 提供结构化变量列表时改为直接使用
    """
    display_name: str = "Prompt Hub"
    description: str = "Prompt Component that uses LangChain Hub prompts"
    beta = True
    icon = "LangChain"
    trace_type = "prompt"
    name = "LangChain Hub Prompt"

    inputs = [
        SecretStrInput(
            name="langchain_api_key",
            display_name="LangChain API Key",
            info="The LangChain API Key to use.",
            required=True,
        ),
        StrInput(
            name="langchain_hub_prompt",
            display_name="LangChain Hub Prompt",
            info="The LangChain Hub prompt to use, i.e., 'efriis/my-first-prompt'",
            refresh_button=True,
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Build Prompt", name="prompt", method="build_prompt"),
    ]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """根据 Hub 模板动态更新构建配置。

        关键路径（三步）：
        1) 仅在 `langchain_hub_prompt` 变更时触发
        2) 拉取模板并解析占位符
        3) 注入动态 `param_*` 输入字段

        异常流：模板拉取失败会向上抛出。
        排障入口：异常文本提示缺少 API Key 或 Hub 拉取失败。
        决策：仅在字段变化时更新配置
        问题：频繁刷新会丢失用户输入
        方案：对 `field_name/field_value` 做短路判断
        代价：无法在其他字段变化时自动刷新
        重评：当引入显式刷新按钮时改为强制刷新
        """
        # 非 `langchain_hub_prompt` 或值为空时不更新
        if field_name != "langchain_hub_prompt" or not field_value:
            return build_config

        # 拉取模板
        template = self._fetch_langchain_hub_template()

        # 获取模板的消息结构
        if hasattr(template, "messages"):
            template_messages = template.messages
        else:
            template_messages = [HumanMessagePromptTemplate(prompt=template)]

        # 提取消息列表的 prompt 数据
        prompt_template = [message_data.prompt for message_data in template_messages]

        # 正则匹配 `{...}` 形式的占位符
        pattern = r"\{(.*?)\}"

        # 收集所有自定义字段
        custom_fields: list[str] = []
        full_template = ""
        for message in prompt_template:
            # 查找匹配项
            matches = re.findall(pattern, message.template)
            custom_fields += matches

            # 构造完整模板文本用于展示
            full_template = full_template + "\n" + message.template

        # 若已存在对应参数则无需重复处理
        if all("param_" + custom_field in build_config for custom_field in custom_fields):
            return build_config

        # 将完整模板展示在 info 弹窗中
        build_config["langchain_hub_prompt"]["info"] = full_template

        # 清理旧的参数输入
        for key in build_config.copy():
            if key.startswith("param_"):
                del build_config[key]

        # 为每个占位符创建输入字段
        for custom_field in custom_fields:
            new_parameter = DefaultPromptField(
                name=f"param_{custom_field}",
                display_name=custom_field,
                info="Fill in the value for {" + custom_field + "}",
            ).to_dict()

            # 注入新的输入字段
            build_config[f"param_{custom_field}"] = new_parameter

        return build_config

    async def build_prompt(
        self,
    ) -> Message:
        """构建最终提示词消息。

        关键路径（三步）：
        1) 拉取 Hub 模板
        2) 读取 `param_*` 并渲染模板
        3) 生成 `Message` 并更新状态

        异常流：Hub 拉取或模板渲染失败会向上抛出。
        排障入口：异常提示缺少 API Key 或模板不存在。
        决策：保持未提供参数的占位符原样输出
        问题：用户可能未填写所有参数
        方案：使用 `{param}` 回退值
        代价：下游可能收到未渲染占位符
        重评：当需要强校验时改为缺参直接报错
        """
        # 拉取模板
        template = self._fetch_langchain_hub_template()

        # 读取参数并填充模板
        params_dict = {param: getattr(self, "param_" + param, f"{{{param}}}") for param in template.input_variables}
        original_params = {k: v.text if hasattr(v, "text") else v for k, v in params_dict.items() if v is not None}
        prompt_value = template.invoke(original_params)

        # 将渲染后的模板写回参数
        original_params["template"] = prompt_value.to_string()

        # 生成消息对象
        prompt = Message.from_template(**original_params)

        self.status = prompt.text

        return prompt

    def _fetch_langchain_hub_template(self):
        """拉取 LangChain Hub 模板。

        契约：输入 `langchain_api_key/langchain_hub_prompt`；输出模板对象；
        副作用：访问外部网络；失败语义：缺少 API Key 抛 `ValueError`。
        关键路径：1) 校验 API Key 2) 调用 `langchain.hub.pull`。
        决策：在本地校验 API Key
        问题：避免无效请求导致的额外网络开销
        方案：缺失时直接报错
        代价：无法在无 Key 模式下预览
        重评：当支持匿名预览时放宽校验
        """
        import langchain.hub

        # 校验 API Key
        if not self.langchain_api_key:
            msg = "Please provide a LangChain API Key"

            raise ValueError(msg)

        # 从 Hub 拉取模板
        return langchain.hub.pull(self.langchain_hub_prompt, api_key=self.langchain_api_key)
