"""
模块名称：`LLM` 条件路由组件

本模块使用模型对输入文本进行分类，并根据分类结果动态路由到不同输出。
主要功能包括：
- 依据路由表生成分类提示词
- 为每个分类动态创建输出端口
- 支持 `Else` 分支与覆盖输出

关键组件：
- `process_case`：分类与主路由
- `default_response`：Else 分支判定
- `update_outputs`：动态输出生成

设计背景：低代码流程需要可配置的语义路由。
注意事项：分类输出需严格匹配 `route_category`，否则视为无匹配。
"""

from typing import Any

from lfx.base.models.unified_models import (
    get_language_model_options,
    get_llm,
    update_model_options_in_build_config,
)
from lfx.custom import Component
from lfx.io import (
    BoolInput,
    MessageInput,
    MessageTextInput,
    ModelInput,
    MultilineInput,
    Output,
    SecretStrInput,
    TableInput,
)
from lfx.schema.message import Message
from lfx.schema.table import EditMode


class SmartRouterComponent(Component):
    """基于 `LLM` 的路由组件。

    契约：输入文本与路由表，输出匹配路由的 `Message`。
    决策：使用模型分类而非规则引擎。
    问题：路由条件语义化且难以穷举规则。
    方案：以提示词引导模型返回类别名进行匹配。
    代价：分类结果受模型漂移影响，可能出现误路由。
    重评：当路由规则稳定且误差不可接受时引入显式规则引擎。
    """
    display_name = "Smart Router"
    description = "Routes an input message using LLM-based categorization."
    icon = "route"
    name = "SmartRouter"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._matched_category = None

    inputs = [
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Model Provider API key",
            real_time_refresh=True,
            advanced=True,
        ),
        MessageTextInput(
            name="input_text",
            display_name="Input",
            info="The primary text input for the operation.",
            required=True,
        ),
        TableInput(
            name="routes",
            display_name="Routes",
            info=(
                "Define the categories for routing. Each row should have a route/category name "
                "and optionally a custom output value."
            ),
            table_schema=[
                {
                    "name": "route_category",
                    "display_name": "Route Name",
                    "type": "str",
                    "description": "Name for the route (used for both output name and category matching)",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "route_description",
                    "display_name": "Route Description",
                    "type": "str",
                    "description": "Description of when this route should be used (helps LLM understand the category)",
                    "default": "",
                    "edit_mode": EditMode.POPOVER,
                },
                {
                    "name": "output_value",
                    "display_name": "Route Message (Optional)",
                    "type": "str",
                    "description": (
                        "Optional message to send when this route is matched."
                        "Leave empty to pass through the original input text."
                    ),
                    "default": "",
                    "edit_mode": EditMode.POPOVER,
                },
            ],
            value=[
                {
                    "route_category": "Positive",
                    "route_description": "Positive feedback, satisfaction, or compliments",
                    "output_value": "",
                },
                {
                    "route_category": "Negative",
                    "route_description": "Complaints, issues, or dissatisfaction",
                    "output_value": "",
                },
            ],
            real_time_refresh=True,
            required=True,
        ),
        MessageInput(
            name="message",
            display_name="Override Output",
            info=(
                "Optional override message that will replace both the Input and Output Value "
                "for all routes when filled."
            ),
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="enable_else_output",
            display_name="Include Else Output",
            info="Include an Else output for cases that don't match any route.",
            value=False,
            advanced=True,
        ),
        MultilineInput(
            name="custom_prompt",
            display_name="Additional Instructions",
            info=(
                "Additional instructions for LLM-based categorization. "
                "These will be added to the base prompt. "
                "Use {input_text} for the input text and {routes} for the available categories."
            ),
            advanced=True,
        ),
    ]

    outputs: list[Output] = []

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """根据用户输入刷新模型选项。"""
        return update_model_options_in_build_config(
            component=self,
            build_config=build_config,
            cache_key_prefix="language_model_options",
            get_options_func=get_language_model_options,
            field_name=field_name,
            field_value=field_value,
        )

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        """为路由表动态生成输出端口。

        契约：根据 `routes` 和 `enable_else_output` 重建输出列表。
        """
        if field_name in {"routes", "enable_else_output"}:
            frontend_node["outputs"] = []

            # 实现：优先使用前端传入的 `routes`，否则读取组件状态。
            routes_data = field_value if field_name == "routes" else getattr(self, "routes", [])

            # 实现：为每个分类创建输出端口，统一走 `process_case`。
            for i, row in enumerate(routes_data):
                route_category = row.get("route_category", f"Category {i + 1}")
                frontend_node["outputs"].append(
                    Output(
                        display_name=route_category,
                        name=f"category_{i + 1}_result",
                        method="process_case",
                        group_outputs=True,
                    )
                )
        # 注意：仅在启用 `Else` 时追加默认输出。
            if field_name == "enable_else_output":
                enable_else = field_value
            else:
                enable_else = getattr(self, "enable_else_output", False)

            if enable_else:
                frontend_node["outputs"].append(
                    Output(display_name="Else", name="default_result", method="default_response", group_outputs=True)
                )
        return frontend_node

    def process_case(self) -> Message:
        """执行分类并输出匹配路由的结果。

        关键路径（三步）：
        1) 构造分类提示词（含可选自定义指令）
        2) 调用模型获取类别名
        3) 匹配路由并停止其它输出
        异常流：`RuntimeError` 会记录到状态但不中断流程。
        排障入口：`status` 字段记录提示词与比对过程。
        """
        # 实现：每次执行前清理匹配状态，避免复用旧值。
        self._matched_category = None

        # 实现：读取路由表与输入文本。
        categories = getattr(self, "routes", [])
        input_text = getattr(self, "input_text", "")

        # 实现：通过模型分类匹配路由。
        matched_category = None
        llm = get_llm(model=self.model, user_id=self.user_id, api_key=self.api_key)

        if llm and categories:
            # 实现：整理分类描述，提升模型理解。
            category_info = []
            for i, category in enumerate(categories):
                cat_name = category.get("route_category", f"Category {i + 1}")
                cat_desc = category.get("route_description", "")
                if cat_desc and cat_desc.strip():
                    category_info.append(f'"{cat_name}": {cat_desc}')
                else:
                    category_info.append(f'"{cat_name}"')

            categories_text = "\n".join([f"- {info}" for info in category_info if info])

            # 实现：构造基础分类提示词。
            base_prompt = (
                f"You are a text classifier. Given the following text and categories, "
                f"determine which category best matches the text.\n\n"
                f'Text to classify: "{input_text}"\n\n'
                f"Available categories:\n{categories_text}\n\n"
                f"Respond with ONLY the exact category name that best matches the text. "
                f'If none match well, respond with "NONE".\n\n'
                f"Category:"
            )

            # 注意：自定义指令会追加到基础提示词后。
            custom_prompt = getattr(self, "custom_prompt", "")
            if custom_prompt and custom_prompt.strip():
                self.status = "Using custom prompt as additional instructions"
                # 实现：将路由名压缩为简单列表供模板使用。
                simple_routes = ", ".join(
                    [f'"{cat.get("route_category", f"Category {i + 1}")}"' for i, cat in enumerate(categories)]
                )
                formatted_custom = custom_prompt.format(input_text=input_text, routes=simple_routes)
                # 实现：合并基础提示词与自定义指令。
                prompt = f"{base_prompt}\n\nAdditional Instructions:\n{formatted_custom}"
            else:
                self.status = "Using default prompt for LLM categorization"
                prompt = base_prompt

            # 排障：记录最终提示词。
            self.status = f"Prompt sent to LLM:\n{prompt}"

            try:
                # 实现：调用模型并获取分类结果。
                if hasattr(llm, "invoke"):
                    response = llm.invoke(prompt)
                    if hasattr(response, "content"):
                        categorization = response.content.strip().strip('"')
                    else:
                        categorization = str(response).strip().strip('"')
                else:
                    categorization = str(llm(prompt)).strip().strip('"')

                # 排障：记录模型输出。
                self.status = f"LLM response: '{categorization}'"

                # 实现：逐一对比分类名（忽略大小写）。
                for i, category in enumerate(categories):
                    route_category = category.get("route_category", "")

                    # 排障：记录每次对比。
                    self.status = (
                        f"Comparing '{categorization}' with category {i + 1}: route_category='{route_category}'"
                    )

                    # 注意：仅接受完全匹配，避免误路由。
                    if categorization.lower() == route_category.lower():
                        matched_category = i
                        self.status = f"MATCH FOUND! Category {i + 1} matched with '{categorization}'"
                        break

                if matched_category is None:
                    self.status = (
                        f"No match found for '{categorization}'. Available categories: "
                        f"{[category.get('route_category', '') for category in categories]}"
                    )

            except RuntimeError as e:
                self.status = f"Error in LLM categorization: {e!s}"
        else:
            self.status = "No LLM provided for categorization"

        if matched_category is not None:
            # 实现：保存匹配结果，供 `Else` 分支判断。
            self._matched_category = matched_category

            # 注意：仅保留匹配输出，停止其它分支。
            for i in range(len(categories)):
                if i != matched_category:
                    self.stop(f"category_{i + 1}_result")

            # 注意：匹配成功时停止 `Else` 分支。
            enable_else = getattr(self, "enable_else_output", False)
            if enable_else:
                self.stop("default_result")

            route_category = categories[matched_category].get("route_category", f"Category {matched_category + 1}")
            self.status = f"Categorized as {route_category}"

            # 注意：覆盖输出优先生效。
            override_output = getattr(self, "message", None)
            if (
                override_output
                and hasattr(override_output, "text")
                and override_output.text
                and str(override_output.text).strip()
            ):
                return Message(text=str(override_output.text))
            if override_output and isinstance(override_output, str) and override_output.strip():
                return Message(text=str(override_output))

            # 实现：优先使用路由自定义输出。
            custom_output = categories[matched_category].get("output_value", "")
            # 注意：`None`/空字符串/空白视为未配置。
            if custom_output and str(custom_output).strip() and str(custom_output).strip().lower() != "none":
                return Message(text=str(custom_output))
            # 注意：未配置输出时回传输入。
            return Message(text=input_text)
        # 注意：无匹配时停止所有分类输出。
        for i in range(len(categories)):
            self.stop(f"category_{i + 1}_result")

        # 注意：`Else` 启用时交由 `default_response` 处理。
        enable_else = getattr(self, "enable_else_output", False)
        if enable_else:
            self.stop("process_case")
            return Message(text="")
        # 注意：`Else` 关闭时无输出。
        self.status = "No match found and Else output is disabled"
        return Message(text="")

    def default_response(self) -> Message:
        """处理无匹配时的 Else 分支。"""
        # 注意：`Else` 未启用直接返回空消息。
        enable_else = getattr(self, "enable_else_output", False)
        if not enable_else:
            self.status = "Else output is disabled"
            return Message(text="")

        # 实现：确保匹配状态存在，避免属性缺失。
        if not hasattr(self, "_matched_category"):
            self._matched_category = None

        categories = getattr(self, "routes", [])
        input_text = getattr(self, "input_text", "")

        # 注意：若主路由已匹配则停止 `Else` 分支。
        if hasattr(self, "_matched_category") and self._matched_category is not None:
            self.status = (
                f"Match already found in process_case (Category {self._matched_category + 1}), "
                "stopping default_response"
            )
            self.stop("default_result")
            return Message(text="")

        # 实现：再做一次分类，确认是否存在匹配。
        has_match = False
        llm = get_llm(model=self.model, user_id=self.user_id, api_key=self.api_key)

        if llm and categories:
            try:
                # 实现：构造分类提示词。
                category_info = []
                for i, category in enumerate(categories):
                    cat_name = category.get("route_category", f"Category {i + 1}")
                    cat_desc = category.get("route_description", "")
                    if cat_desc and cat_desc.strip():
                        category_info.append(f'"{cat_name}": {cat_desc}')
                    else:
                        category_info.append(f'"{cat_name}"')

                categories_text = "\n".join([f"- {info}" for info in category_info if info])

                # 实现：基础提示词与主路由一致。
                base_prompt = (
                    "You are a text classifier. Given the following text and categories, "
                    "determine which category best matches the text.\n\n"
                    f'Text to classify: "{input_text}"\n\n'
                    f"Available categories:\n{categories_text}\n\n"
                    "Respond with ONLY the exact category name that best matches the text. "
                    'If none match well, respond with "NONE".\n\n'
                    "Category:"
                )

                # 注意：追加自定义指令以保持一致性。
                custom_prompt = getattr(self, "custom_prompt", "")
                if custom_prompt and custom_prompt.strip():
                    self.status = "Using custom prompt as additional instructions (default check)"
                    # 实现：构造简化路由列表供模板使用。
                    simple_routes = ", ".join(
                        [f'"{cat.get("route_category", f"Category {i + 1}")}"' for i, cat in enumerate(categories)]
                    )
                    formatted_custom = custom_prompt.format(input_text=input_text, routes=simple_routes)
                    # 实现：合并提示词。
                    prompt = f"{base_prompt}\n\nAdditional Instructions:\n{formatted_custom}"
                else:
                    self.status = "Using default prompt for LLM categorization (default check)"
                    prompt = base_prompt

                # 排障：记录默认检查提示词。
                self.status = f"Default check - Prompt sent to LLM:\n{prompt}"

                # 实现：调用模型并获取分类结果。
                if hasattr(llm, "invoke"):
                    response = llm.invoke(prompt)
                    if hasattr(response, "content"):
                        categorization = response.content.strip().strip('"')
                    else:
                        categorization = str(response).strip().strip('"')
                else:
                    categorization = str(llm(prompt)).strip().strip('"')

                # 排障：记录默认检查结果。
                self.status = f"Default check - LLM response: '{categorization}'"

                # 实现：逐一匹配分类。
                for i, category in enumerate(categories):
                    route_category = category.get("route_category", "")

                    # 排障：记录比对过程。
                    self.status = (
                        f"Default check - Comparing '{categorization}' with category {i + 1}: "
                        f"route_category='{route_category}'"
                    )

                    if categorization.lower() == route_category.lower():
                        has_match = True
                        self.status = f"Default check - MATCH FOUND! Category {i + 1} matched with '{categorization}'"
                        break

                if not has_match:
                    self.status = (
                        f"Default check - No match found for '{categorization}'. "
                        f"Available categories: "
                        f"{[category.get('route_category', '') for category in categories]}"
                    )

            except RuntimeError:
                pass  # 注意：默认按无匹配处理，避免阻断流程。

        if has_match:
            # 注意：存在匹配时停止 `Else` 输出。
            self.stop("default_result")
            return Message(text="")

        # 注意：无匹配时优先覆盖输出，否则回传输入。
        override_output = getattr(self, "message", None)
        if (
            override_output
            and hasattr(override_output, "text")
            and override_output.text
            and str(override_output.text).strip()
        ):
            self.status = "Routed to Else (no match) - using override output"
            return Message(text=str(override_output.text))
        if override_output and isinstance(override_output, str) and override_output.strip():
            self.status = "Routed to Else (no match) - using override output"
            return Message(text=str(override_output))
        self.status = "Routed to Else (no match) - using input as default"
        return Message(text=input_text)
