"""
模块名称：结构化输出组件

本模块使用模型从非结构化文本中提取结构化结果，支持输出 `Data` 与 `DataFrame`。
主要功能包括：
- 根据表格 schema 动态构建 `Pydantic` 模型
- 先尝试 `trustcall` 工具调用，失败则回退到 `with_structured_output`
- 将结果规范化为列表/单对象

关键组件：
- `build_structured_output_base`：统一抽取入口
- `_extract_output_with_trustcall`：工具调用优先路径
- `_extract_output_with_langchain`：回退路径

设计背景：抽取型任务需要稳定结构化输出，避免自由文本漂移。
注意事项：模型不支持工具调用时会自动回退，日志中会出现 `Trustcall extraction failed`。
"""

from pydantic import BaseModel, Field, create_model
from trustcall import create_extractor

from lfx.base.models.chat_result import get_chat_result
from lfx.base.models.unified_models import (
    get_language_model_options,
    get_llm,
    update_model_options_in_build_config,
)
from lfx.custom.custom_component.component import Component
from lfx.helpers.base_model import build_model_from_schema
from lfx.io import (
    MessageTextInput,
    ModelInput,
    MultilineInput,
    Output,
    SecretStrInput,
    TableInput,
)
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.table import EditMode


class StructuredOutputComponent(Component):
    """结构化抽取组件入口。

    契约：输入文本与 schema，输出结构化对象或列表。
    决策：先走 `trustcall` 再回退 `with_structured_output`。
    问题：不同模型对工具调用支持不一致。
    方案：优先使用更稳的工具调用，失败再回退。
    代价：双路径实现复杂度提高，日志更嘈杂。
    重评：当全量模型支持一致时可去除回退路径。
    """
    display_name = "Structured Output"
    description = "Uses an LLM to generate structured data. Ideal for extraction and consistency."
    documentation: str = "https://docs.langflow.org/structured-output"
    name = "StructuredOutput"
    icon = "braces"

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
        MultilineInput(
            name="input_value",
            display_name="Input Message",
            info="The input message to the language model.",
            tool_mode=True,
            required=True,
        ),
        MultilineInput(
            name="system_prompt",
            display_name="Format Instructions",
            info="The instructions to the language model for formatting the output.",
            value=(
                "You are an AI that extracts structured JSON objects from unstructured text. "
                "Use a predefined schema with expected types (str, int, float, bool, dict). "
                "Extract ALL relevant instances that match the schema - if multiple patterns exist, capture them all. "
                "Fill missing or ambiguous values with defaults: null for missing values. "
                "Remove exact duplicates but keep variations that have different field values. "
                "Always return valid JSON in the expected format, never throw errors. "
                "If multiple objects can be extracted, return them all in the structured format."
            ),
            required=True,
            advanced=True,
        ),
        MessageTextInput(
            name="schema_name",
            display_name="Schema Name",
            info="Provide a name for the output data schema.",
            advanced=True,
        ),
        TableInput(
            name="output_schema",
            display_name="Output Schema",
            info="Define the structure and data types for the model's output.",
            required=True,
            # 注意：后续移除默认值以避免误导配置。
            table_schema=[
                {
                    "name": "name",
                    "display_name": "Name",
                    "type": "str",
                    "description": "Specify the name of the output field.",
                    "default": "field",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "description",
                    "display_name": "Description",
                    "type": "str",
                    "description": "Describe the purpose of the output field.",
                    "default": "description of field",
                    "edit_mode": EditMode.POPOVER,
                },
                {
                    "name": "type",
                    "display_name": "Type",
                    "type": "str",
                    "edit_mode": EditMode.INLINE,
                    "description": ("Indicate the data type of the output field (e.g., str, int, float, bool, dict)."),
                    "options": ["str", "int", "float", "bool", "dict"],
                    "default": "str",
                },
                {
                    "name": "multiple",
                    "display_name": "As List",
                    "type": "boolean",
                    "description": "Set to True if this output field should be a list of the specified type.",
                    "default": "False",
                    "edit_mode": EditMode.INLINE,
                },
            ],
            value=[
                {
                    "name": "field",
                    "description": "description of field",
                    "type": "str",
                    "multiple": "False",
                }
            ],
        ),
    ]

    outputs = [
        Output(
            name="structured_output",
            display_name="Structured Output",
            method="build_structured_output",
        ),
        Output(
            name="dataframe_output",
            display_name="Structured Output",
            method="build_structured_dataframe",
        ),
    ]

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

    def build_structured_output_base(self):
        """抽取结构化结果并标准化输出。

        关键路径（三步）：
        1) 构建 `Pydantic` schema 与运行配置
        2) 尝试 `trustcall` 抽取，失败回退
        3) 归一化为对象列表或原始结果
        异常流：模型不支持结构化输出时抛 `TypeError`/`ValueError`。
        """
        schema_name = self.schema_name or "OutputModel"

        llm = get_llm(model=self.model, user_id=self.user_id, api_key=self.api_key)

        if not hasattr(llm, "with_structured_output"):
            msg = "Language model does not support structured output."
            raise TypeError(msg)
        if not self.output_schema:
            msg = "Output schema cannot be empty"
            raise ValueError(msg)

        output_model_ = build_model_from_schema(self.output_schema)
        output_model = create_model(
            schema_name,
            __doc__=f"A list of {schema_name}.",
            objects=(
                list[output_model_],
                Field(
                    description=f"A list of {schema_name}.",  # type: ignore[valid-type]
                    min_length=1,  # 注意：保证非空输出
                ),
            ),
        )
        # 注意：传递追踪配置以便链路观测。
        config_dict = {
            "run_name": self.display_name,
            "project_name": self.get_project_name(),
            "callbacks": self.get_langchain_callbacks(),
        }
        # 决策：优先 `trustcall`，失败后回退 `with_structured_output`。
        result = self._extract_output_with_trustcall(llm, output_model, config_dict)
        if result is None:
            result = self._extract_output_with_langchain(llm, output_model, config_dict)

        # 注意：基于 `trustcall` 响应结构做简化处理，同时保留防御性分支。
        if not isinstance(result, dict):
            return result

        # 实现：提取首个响应并在必要时转为字典。
        responses = result.get("responses", [])
        if not responses:
            return result

        # 实现：`BaseModel` 转字典以获取 `objects`。
        first_response = responses[0]
        structured_data = first_response
        if isinstance(first_response, BaseModel):
            structured_data = first_response.model_dump()
        # 注意：`objects` 键由 schema 保证存在。
        return structured_data.get("objects", structured_data)

    def build_structured_output(self) -> Data:
        """返回结构化 `Data` 输出。"""
        output = self.build_structured_output_base()
        if not isinstance(output, list) or not output:
            # 注意：空结果或类型异常直接失败。
            msg = "No structured output returned"
            raise ValueError(msg)
        if len(output) == 1:
            return Data(data=output[0])
        if len(output) > 1:
            # 实现：多结果统一封装为 `results`。
            return Data(data={"results": output})
        return Data()

    def build_structured_dataframe(self) -> DataFrame:
        """返回结构化 `DataFrame` 输出。"""
        output = self.build_structured_output_base()
        if not isinstance(output, list) or not output:
            # 注意：空结果或类型异常直接失败。
            msg = "No structured output returned"
            raise ValueError(msg)
        if len(output) == 1:
            # 实现：单条结果包一层列表以生成单行表。
            return DataFrame([output[0]])
        if len(output) > 1:
            # 实现：多条结果直接转表。
            return DataFrame(output)
        return DataFrame()

    def _extract_output_with_trustcall(self, llm, schema: BaseModel, config_dict: dict) -> list[BaseModel] | None:
        """使用 `trustcall` 抽取结构化结果。"""
        try:
            llm_with_structured_output = create_extractor(llm, tools=[schema], tool_choice=schema.__name__)
            result = get_chat_result(
                runnable=llm_with_structured_output,
                system_message=self.system_prompt,
                input_value=self.input_value,
                config=config_dict,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Trustcall extraction failed, falling back to Langchain: {e} "
                "(Note: This may not be an error—some models or configurations do not support tool calling. "
                "Falling back is normal in such cases.)"
            )
            return None
        return result or None  # 注意：空结果触发回退。

    def _extract_output_with_langchain(self, llm, schema: BaseModel, config_dict: dict) -> list[BaseModel] | None:
        """使用 `with_structured_output` 抽取结构化结果。"""
        try:
            llm_with_structured_output = llm.with_structured_output(schema)
            result = get_chat_result(
                runnable=llm_with_structured_output,
                system_message=self.system_prompt,
                input_value=self.input_value,
                config=config_dict,
            )
            if isinstance(result, BaseModel):
                result = result.model_dump()
                result = result.get("objects", result)
        except Exception as fallback_error:
            msg = (
                f"Model does not support tool calling (trustcall failed) "
                f"and fallback with_structured_output also failed: {fallback_error}"
            )
            raise ValueError(msg) from fallback_error

        return result or None
