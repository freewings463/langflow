"""
模块名称：基于 `OpenRouter` 规格的 `LLM` 选择器

本模块使用“判别模型”对候选模型进行评分与选择，并可从 `OpenRouter` 拉取规格辅助决策。
主要功能包括：
- 拉取并缓存模型规格，建立名称映射
- 构造包含规格的判别提示词
- 失败时按配置回退到第一个模型

关键组件：
- `_fetch_openrouter_models_data`：规格拉取与缓存
- `route_to_model`：主路由与回退逻辑
- `_parse_judge_response`：判别结果解析

设计背景：在多模型场景中需要根据任务与偏好动态选择模型。
注意事项：外部 `API` 超时/限流会触发降级或回退。
"""

import asyncio
import http  # 引入 `HTTPStatus` 常量
import json
from typing import Any

import aiohttp

from lfx.base.models.chat_result import get_chat_result
from lfx.base.models.model_utils import get_model_name
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, HandleInput, IntInput, MultilineInput
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.template.field.base import Output


class LLMSelectorComponent(Component):
    """模型路由与选择组件。

    契约：输入候选模型、用户问题与判别模型，输出最优模型结果。
    决策：由“判别模型”选择候选模型而非静态规则。
    问题：模型能力与成本动态变化，手写规则难以维护。
    方案：将规格与问题拼装成提示词，让判别模型返回索引。
    代价：判别模型本身也可能偏差，需提供回退策略。
    重评：当选择结果稳定且可规则化时引入静态路由。
    """
    display_name = "LLM Selector"
    description = "Routes the input to the most appropriate LLM based on OpenRouter model specifications"
    documentation: str = "https://docs.langflow.org/llm-selector"
    icon = "git-branch"

    # 注意：将魔法数集中，避免分散修改。
    MAX_DESCRIPTION_LENGTH = 500
    QUERY_PREVIEW_MAX_LENGTH = 1000

    inputs = [
        HandleInput(
            name="models",
            display_name="Language Models",
            input_types=["LanguageModel"],
            required=True,
            is_list=True,
            info="List of LLMs to route between",
        ),
        MultilineInput(
            name="input_value",
            display_name="Input",
            required=True,
            info="The input message to be routed",
        ),
        HandleInput(
            name="judge_llm",
            display_name="Judge LLM",
            input_types=["LanguageModel"],
            required=True,
            info="LLM that will evaluate and select the most appropriate model",
        ),
        DropdownInput(
            name="optimization",
            display_name="Optimization",
            options=["quality", "speed", "cost", "balanced"],
            value="balanced",
            info="Optimization preference for model selection",
        ),
        BoolInput(
            name="use_openrouter_specs",
            display_name="Use OpenRouter Specs",
            value=True,
            info=(
                "Fetch model specifications from OpenRouter API for enhanced routing decisions. "
                "If false, only model names will be used."
            ),
            advanced=True,
        ),
        IntInput(
            name="timeout",
            display_name="API Timeout",
            value=10,
            info="Timeout for API requests in seconds",
            advanced=True,
        ),
        BoolInput(
            name="fallback_to_first",
            display_name="Fallback to First Model",
            value=True,
            info="Use first model as fallback when routing fails",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="route_to_model"),
        Output(
            display_name="Selected Model Info",
            name="selected_model_info",
            method="get_selected_model_info",
            types=["Data"],
        ),
        Output(
            display_name="Routing Decision",
            name="routing_decision",
            method="get_routing_decision",
        ),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._selected_model_name: str | None = None
        self._selected_api_model_id: str | None = None
        self._routing_decision: str = ""
        self._models_api_cache: dict[str, dict[str, Any]] = {}
        self._model_name_to_api_id: dict[str, str] = {}

    def _simplify_model_name(self, name: str) -> str:
        """规范化模型名，提升映射命中率。"""
        return "".join(c.lower() for c in name if c.isalnum())

    async def _fetch_openrouter_models_data(self) -> None:
        """从 `OpenRouter` 拉取模型规格并构建映射缓存。

        失败语义：网络/解析失败时清空缓存并记录日志。
        """
        if self._models_api_cache and self._model_name_to_api_id:
            return

        if not self.use_openrouter_specs:
            self.log("OpenRouter specs are disabled. Skipping fetch.")
            return

        try:
            self.status = "Fetching OpenRouter model specifications..."
            self.log("Fetching all model specifications from OpenRouter API: https://openrouter.ai/api/v1/models")
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session,
                session.get("https://openrouter.ai/api/v1/models") as response,
            ):
                if response.status == http.HTTPStatus.OK:
                    data = await response.json()
                    models_list = data.get("data", [])

                    _models_api_cache_temp = {}
                    _model_name_to_api_id_temp = {}

                    for model_data in models_list:
                        api_model_id = model_data.get("id")
                        if not api_model_id:
                            continue

                        _models_api_cache_temp[api_model_id] = model_data
                        _model_name_to_api_id_temp[api_model_id] = api_model_id

                        api_model_name = model_data.get("name")
                        if api_model_name:
                            _model_name_to_api_id_temp[api_model_name] = api_model_id
                            simplified_api_name = self._simplify_model_name(api_model_name)
                            _model_name_to_api_id_temp[simplified_api_name] = api_model_id

                        hugging_face_id = model_data.get("hugging_face_id")
                        if hugging_face_id:
                            _model_name_to_api_id_temp[hugging_face_id] = api_model_id
                            simplified_hf_id = self._simplify_model_name(hugging_face_id)
                            _model_name_to_api_id_temp[simplified_hf_id] = api_model_id

                        if "/" in api_model_id:
                            try:
                                model_name_part_of_id = api_model_id.split("/", 1)[1]
                                if model_name_part_of_id:
                                    _model_name_to_api_id_temp[model_name_part_of_id] = api_model_id
                                    simplified_part_id = self._simplify_model_name(model_name_part_of_id)
                                    _model_name_to_api_id_temp[simplified_part_id] = api_model_id
                            except IndexError:
                                pass  # 注意：存在 `/` 时理论上不会触发。

                    self._models_api_cache = _models_api_cache_temp
                    self._model_name_to_api_id = _model_name_to_api_id_temp
                    log_msg = (
                        f"Successfully fetched and cached {len(self._models_api_cache)} "
                        f"model specifications from OpenRouter."
                    )
                    self.log(log_msg)
                else:
                    err_text = await response.text()
                    self.log(f"Failed to fetch OpenRouter models: HTTP {response.status} - {err_text}")
                    self._models_api_cache = {}
                    self._model_name_to_api_id = {}
        except aiohttp.ClientError as e:
            self.log(f"AIOHTTP ClientError fetching OpenRouter models: {e!s}", "error")
            self._models_api_cache = {}
            self._model_name_to_api_id = {}
        except asyncio.TimeoutError:
            self.log("Timeout fetching OpenRouter model specifications.", "error")
            self._models_api_cache = {}
            self._model_name_to_api_id = {}
        except json.JSONDecodeError as e:
            self.log(f"JSON decode error fetching OpenRouter models: {e!s}", "error")
            self._models_api_cache = {}
            self._model_name_to_api_id = {}
        finally:
            self.status = ""

    def _get_api_model_id_for_langflow_model(self, langflow_model_name: str) -> str | None:
        """根据 Langflow 模型名映射 `OpenRouter` 的 API ID。"""
        if not langflow_model_name:
            return None

        potential_names_to_check = [langflow_model_name, self._simplify_model_name(langflow_model_name)]

        if langflow_model_name.startswith("models/"):
            name_without_prefix = langflow_model_name[len("models/") :]
            potential_names_to_check.append(name_without_prefix)
            potential_names_to_check.append(self._simplify_model_name(name_without_prefix))

        elif langflow_model_name.startswith("community_models/"):
            name_without_prefix = langflow_model_name[len("community_models/") :]
            potential_names_to_check.append(name_without_prefix)
            simplified_no_prefix = self._simplify_model_name(name_without_prefix)
            potential_names_to_check.append(simplified_no_prefix)

        elif langflow_model_name.startswith("community_models/"):
            name_without_prefix = langflow_model_name[len("community_models/") :]
            potential_names_to_check.append(name_without_prefix)
            simplified_no_prefix_comm = self._simplify_model_name(name_without_prefix)
            potential_names_to_check.append(simplified_no_prefix_comm)

        unique_names_to_check = list(dict.fromkeys(potential_names_to_check))

        for name_variant in unique_names_to_check:
            if name_variant in self._model_name_to_api_id:
                return self._model_name_to_api_id[name_variant]

        self.log(
            f"Could not map Langflow model name '{langflow_model_name}' "
            f"(tried variants: {unique_names_to_check}) to an OpenRouter API ID."
        )
        return None

    def _get_model_specs_dict(self, langflow_model_name: str) -> dict[str, Any]:
        """获取模型规格字典，供判别模型使用。"""
        if not self.use_openrouter_specs or not self._models_api_cache:
            return {
                "id": langflow_model_name,
                "name": langflow_model_name,
                "description": "Specifications not available.",
            }

        api_model_id = self._get_api_model_id_for_langflow_model(langflow_model_name)

        if not api_model_id or api_model_id not in self._models_api_cache:
            log_msg = (
                f"No cached API data found for Langflow model '{langflow_model_name}' "
                f"(mapped API ID: {api_model_id}). Returning basic info."
            )
            self.log(log_msg)
            return {
                "id": langflow_model_name,
                "name": langflow_model_name,
                "description": "Full specifications not found in cache.",
            }

        model_data = self._models_api_cache[api_model_id]
        top_provider_data = model_data.get("top_provider", {})
        architecture_data = model_data.get("architecture", {})
        pricing_data = model_data.get("pricing", {})
        description = model_data.get("description", "No description available")
        truncated_description = (
            description[: self.MAX_DESCRIPTION_LENGTH - 3] + "..."
            if len(description) > self.MAX_DESCRIPTION_LENGTH
            else description
        )

        specs = {
            "id": model_data.get("id"),
            "name": model_data.get("name"),
            "description": truncated_description,
            "context_length": top_provider_data.get("context_length") or model_data.get("context_length"),
            "max_completion_tokens": (
                top_provider_data.get("max_completion_tokens") or model_data.get("max_completion_tokens")
            ),
            "tokenizer": architecture_data.get("tokenizer"),
            "input_modalities": architecture_data.get("input_modalities", []),
            "output_modalities": architecture_data.get("output_modalities", []),
            "pricing_prompt": pricing_data.get("prompt"),
            "pricing_completion": pricing_data.get("completion"),
            "is_moderated": top_provider_data.get("is_moderated"),
            "supported_parameters": model_data.get("supported_parameters", []),
        }
        return {k: v for k, v in specs.items() if v is not None}

    def _create_system_prompt(self) -> str:
        """生成判别模型的系统提示词。"""
        return """\
You are an expert AI model selection specialist. Your task is to analyze the user's input query,
their optimization preference, and a list of available models with their specifications,
then select the most appropriate model.

Each model will be presented as a JSON object with its capabilities and characteristics.

Your decision should be based on:
1. Task complexity and requirements derived from the user's query.
2. Context length needed for the input.
3. Model capabilities (e.g., context window, input/output modalities, tokenizer).
4. Pricing considerations, if relevant to the optimization preference.
5. User's stated optimization preference (quality, speed, cost, balanced).

Return ONLY the index number (0, 1, 2, etc.) of the best model from the provided list.
Do not provide any explanation or reasoning, just the index number.
If multiple models seem equally suitable according to the preference, you may pick the first one that matches.
If no model seems suitable, pick the first model in the list (index 0) as a fallback."""

    async def route_to_model(self) -> Message:
        """主路由逻辑：选择模型并生成结果。

        关键路径（三步）：
        1) 拉取规格并构造判别提示词
        2) 解析判别结果确定模型
        3) 调用目标模型生成输出
        决策：默认启用 `fallback_to_first` 以保障可用性。
        问题：外部 API 或判别模型失败会导致流程中断。
        方案：失败时回退到第一个候选模型。
        代价：可能牺牲质量/成本目标。
        重评：当失败率下降且需要严格路由时关闭回退。
        """
        if not self.models or not self.input_value or not self.judge_llm:
            error_msg = "Missing required inputs: models, input_value, or judge_llm"
            self.status = error_msg
            self.log(f"Validation Error: {error_msg}", "error")
            raise ValueError(error_msg)

        successful_result: Message | None = None
        try:
            self.log(f"Starting model routing with {len(self.models)} available Langflow models.")
            self.log(f"Optimization preference: {self.optimization}")
            self.log(f"Input length: {len(self.input_value)} characters")

            if self.use_openrouter_specs and not self._models_api_cache:
                await self._fetch_openrouter_models_data()

            system_prompt_content = self._create_system_prompt()
            system_message = {"role": "system", "content": system_prompt_content}

            self.status = "Analyzing available models and preparing specifications..."
            model_specs_for_judge = []
            for i, langflow_model_instance in enumerate(self.models):
                langflow_model_name = get_model_name(langflow_model_instance)
                if not langflow_model_name:
                    self.log(f"Warning: Could not determine name for model at index {i}. Using placeholder.", "warning")
                    spec_dict = {
                        "id": f"unknown_model_{i}",
                        "name": f"Unknown Model {i}",
                        "description": "Name could not be determined.",
                    }
                else:
                    spec_dict = self._get_model_specs_dict(langflow_model_name)

                model_specs_for_judge.append({"index": i, "langflow_name": langflow_model_name, "specs": spec_dict})
                self.log(
                    f"Prepared specs for Langflow model {i} ('{langflow_model_name}'): {spec_dict.get('name', 'N/A')}"
                )

            estimated_tokens = len(self.input_value.split()) * 1.3
            self.log(f"Estimated input tokens: {int(estimated_tokens)}")

            query_preview = self.input_value[: self.QUERY_PREVIEW_MAX_LENGTH]
            if len(self.input_value) > self.QUERY_PREVIEW_MAX_LENGTH:
                query_preview += "..."

            user_message_content = f"""User Query: "{query_preview}"
Optimization Preference: {self.optimization}
Estimated Input Tokens: ~{int(estimated_tokens)}

Available Models (JSON list):
{json.dumps(model_specs_for_judge, indent=2)}

Based on the user query, optimization preference, and the detailed model specifications,
select the index of the most appropriate model.
Return ONLY the index number:"""

            user_message = {"role": "user", "content": user_message_content}

            self.log("Requesting model selection from judge LLM...")
            self.status = "Judge LLM analyzing options..."

            response = await self.judge_llm.ainvoke([system_message, user_message])
            selected_index, chosen_model_instance = self._parse_judge_response(response.content.strip())
            self._selected_model_name = get_model_name(chosen_model_instance)
            if self._selected_model_name:
                self._selected_api_model_id = (
                    self._get_api_model_id_for_langflow_model(self._selected_model_name) or self._selected_model_name
                )
            else:
                self._selected_api_model_id = "unknown_model"

            specs_source = (
                "OpenRouter API"
                if self.use_openrouter_specs and self._models_api_cache
                else "Basic (Langflow model names only)"
            )
            self._routing_decision = f"""Model Selection Decision:
- Selected Model Index: {selected_index}
- Selected Langflow Model Name: {self._selected_model_name}
- Selected API Model ID (if resolved): {self._selected_api_model_id}
- Optimization Preference: {self.optimization}
- Input Query Length: {len(self.input_value)} characters (~{int(estimated_tokens)} tokens)
- Number of Models Considered: {len(self.models)}
- Specifications Source: {specs_source}"""

            log_msg = (
                f"DECISION by Judge LLM: Selected model index {selected_index} -> "
                f"Langflow Name: '{self._selected_model_name}', API ID: '{self._selected_api_model_id}'"
            )
            self.log(log_msg)

            self.status = f"Generating response with: {self._selected_model_name}"
            input_message_obj = Message(text=self.input_value)

            raw_result = get_chat_result(
                runnable=chosen_model_instance,
                input_value=input_message_obj,
            )
            result = Message(text=str(raw_result)) if not isinstance(raw_result, Message) else raw_result

            self.status = f"Successfully routed to: {self._selected_model_name}"
            successful_result = result

        except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
            error_msg = f"Routing error: {type(e).__name__} - {e!s}"
            self.log(f"{error_msg}", "error")
            self.log("Detailed routing error occurred. Check logs for details.", "error")
            self.status = error_msg

            if self.fallback_to_first and self.models:
                # 注意：路由失败时按配置回退到首个模型。
                self.log("Activating fallback to first model due to error.", "warning")
                chosen_model_instance = self.models[0]
                self._selected_model_name = get_model_name(chosen_model_instance)
                if self._selected_model_name:
                    mapped_id = self._get_api_model_id_for_langflow_model(self._selected_model_name)
                    self._selected_api_model_id = mapped_id or self._selected_model_name
                else:
                    self._selected_api_model_id = "fallback_model"
                self._routing_decision = f"""Fallback Decision:
- Error During Routing: {error_msg}
- Fallback Model Langflow Name: {self._selected_model_name}
- Fallback Model API ID (if resolved): {self._selected_api_model_id}
- Reason: Automatic fallback enabled"""

                self.status = f"Fallback: Using {self._selected_model_name}"
                input_message_obj = Message(text=self.input_value)

                raw_fallback_result = get_chat_result(
                    runnable=chosen_model_instance,
                    input_value=input_message_obj,
                )
                if not isinstance(raw_fallback_result, Message):
                    successful_result = Message(text=str(raw_fallback_result))
                else:
                    successful_result = raw_fallback_result
            else:
                self.log("No fallback model available or fallback disabled. Raising error.", "error")
                raise

        if successful_result is None:
            error_message = "Unexpected state in route_to_model: No result produced."
            self.log(f"Error: {error_message}", "error")
            raise RuntimeError(error_message)
        return successful_result

    def _parse_judge_response(self, response_content: str) -> tuple[int, Any]:
        """解析判别模型的索引输出。"""
        try:
            cleaned_response = "".join(filter(str.isdigit, response_content.strip()))
            if not cleaned_response:
                self.log(f"Judge LLM response was non-numeric: '{response_content}'. Defaulting to index 0.", "warning")
                return 0, self.models[0]

            selected_index = int(cleaned_response)

            if 0 <= selected_index < len(self.models):
                self.log(f"Judge LLM selected index: {selected_index}")
                return selected_index, self.models[selected_index]
            log_msg = (
                f"Judge LLM selected index {selected_index} is out of bounds "
                f"(0-{len(self.models) - 1}). Defaulting to index 0."
            )
            self.log(log_msg, "warning")
            return 0, self.models[0]

        except ValueError:
            self.log(
                f"Could not parse judge LLM response to integer: '{response_content}'. Defaulting to index 0.",
                "warning",
            )
            return 0, self.models[0]
        except (AttributeError, IndexError) as e:
            self.log(f"Error parsing judge response '{response_content}': {e!s}. Defaulting to index 0.", "error")
            return 0, self.models[0]

    def get_selected_model_info(self) -> list[Data]:
        """返回已选模型的规格信息。"""
        if self._selected_model_name:
            specs_dict = self._get_model_specs_dict(self._selected_model_name)
            if "langflow_name" not in specs_dict:
                specs_dict["langflow_model_name_used_for_lookup"] = self._selected_model_name
            if self._selected_api_model_id and specs_dict.get("id") != self._selected_api_model_id:
                specs_dict["resolved_api_model_id"] = self._selected_api_model_id
            data_output = [Data(data=specs_dict)]
            self.status = data_output
            return data_output

        data_output = [Data(data={"info": "No model selected yet - run the router first."})]
        self.status = data_output
        return data_output

    def get_routing_decision(self) -> Message:
        """返回路由决策明细文本。"""
        if self._routing_decision:
            message_output = Message(text=f"{self._routing_decision}")
            self.status = message_output
            return message_output

        message_output = Message(text="No routing decision made yet - run the router first.")
        self.status = message_output
        return message_output
