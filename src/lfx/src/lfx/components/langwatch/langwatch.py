"""
模块名称：LangWatch 评估组件适配器

本模块提供 LangWatch 评估服务的组件封装，用于在 Langflow 中动态加载评估器并发起评估请求。
主要功能包括：
- 拉取评估器列表并缓存；
- 根据评估器 schema 动态生成输入项；
- 组装请求并调用 LangWatch 评估 API。

关键组件：
- LangWatchComponent：组件主体，负责配置、动态输入与评估调用。

设计背景：对接 LangWatch 的外部评估能力，保持 UI/运行时配置一致。
注意事项：依赖 `LANGWATCH_ENDPOINT` 环境变量与网络可达性。
"""

import json
import os
from typing import Any

import httpx

from lfx.base.langwatch.utils import get_cached_evaluators
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MultilineInput
from lfx.io import (
    BoolInput,
    DropdownInput,
    FloatInput,
    IntInput,
    MessageTextInput,
    NestedDictInput,
    Output,
    SecretStrInput,
)
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class LangWatchComponent(Component):
    """LangWatch 评估组件封装

    契约：依赖 `api_key` 与 `LANGWATCH_ENDPOINT`；输出 `Data`（成功为评估结果，失败为 `{"error": ...}`）。
    关键路径：1) 拉取评估器与动态输入 2) 组装 payload 3) 调用评估 API 并返回结果。
    决策：使用 LangWatch 远端评估而非本地规则
    问题：需要统一在线评估并与控制台保持一致
    方案：调用 `/api/evaluations/.../evaluate` 并透传 settings
    代价：依赖网络与外部可用性，超时由 `timeout` 控制
    重评：当评估器迁移到本地或需要离线模式时
    """

    display_name: str = "LangWatch Evaluator"
    description: str = "Evaluates various aspects of language models using LangWatch's evaluation endpoints."
    documentation: str = "https://docs.langwatch.ai/langevals/documentation/introduction"
    icon: str = "Langwatch"
    name: str = "LangWatchEvaluator"

    inputs = [
        DropdownInput(
            name="evaluator_name",
            display_name="Evaluator Name",
            options=[],
            required=True,
            info="Select an evaluator.",
            refresh_button=True,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="LangWatch API Key",
            required=True,
            info="Enter your LangWatch API key.",
        ),
        MessageTextInput(
            name="input",
            display_name="Input",
            required=False,
            info="The input text for evaluation.",
        ),
        MessageTextInput(
            name="output",
            display_name="Output",
            required=False,
            info="The output text for evaluation.",
        ),
        MessageTextInput(
            name="expected_output",
            display_name="Expected Output",
            required=False,
            info="The expected output for evaluation.",
        ),
        MessageTextInput(
            name="contexts",
            display_name="Contexts",
            required=False,
            info="The contexts for evaluation (comma-separated).",
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="The maximum time (in seconds) allowed for the server to respond before timing out.",
            value=30,
            advanced=True,
        ),
    ]

    outputs = [
        Output(name="evaluation_result", display_name="Evaluation Result", method="evaluate"),
    ]

    def set_evaluators(self, endpoint: str):
        """拉取并缓存评估器列表

        契约：输入 `endpoint` 基础地址；副作用：写入 `self.evaluators` 与 `self.status`。
        关键路径：1) 组合 list URL 2) 读取缓存/远端 3) 校验非空。
        异常流：无评估器时抛 `ValueError`，调用方应中止构建流程。
        决策：在组件内拉取列表而非预置常量
        问题：评估器集合随账户/环境变化
        方案：调用 `/api/evaluations/list` 并复用缓存
        代价：首次加载增加一次网络调用
        重评：当评估器列表稳定且可配置化时
        """

        url = f"{endpoint}/api/evaluations/list"
        self.evaluators = get_cached_evaluators(url)
        if not self.evaluators or len(self.evaluators) == 0:
            self.status = f"No evaluators found from {endpoint}"
            msg = f"No evaluators found from {endpoint}"
            raise ValueError(msg)

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None) -> dotdict:
        """更新构建配置并同步动态输入

        契约：`build_config` 可被就地修改；返回更新后的配置。副作用：更新 `self.current_evaluator`/动态属性。
        关键路径（三步）：
        1) 同步评估器列表与默认值
        2) 根据选择清理/重建动态输入
        3) 统一 required/默认键并写回
        异常流：配置缺失或属性异常时写入 `self.status`，不抛异常。
        排障入口：日志 `Updating build config` / `Missing required keys`。
        决策：按评估器切换动态字段而非保留旧字段
        问题：评估器 schema 不同导致 UI 与运行时不一致
        方案：清理非默认键后重建输入
        代价：切换时丢失旧字段值
        重评：需要跨评估器保留用户输入时
        """

        try:
            logger.info(f"Updating build config. Field name: {field_name}, Field value: {field_value}")

            if field_name is None or field_name == "evaluator_name":
                self.evaluators = self.get_evaluators(os.getenv("LANGWATCH_ENDPOINT", "https://app.langwatch.ai"))
                build_config["evaluator_name"]["options"] = list(self.evaluators.keys())

                # 注意：首次加载若无选择，强制设置默认评估器以避免空选导致评估失败。
                if not getattr(self, "current_evaluator", None) and self.evaluators:
                    self.current_evaluator = next(iter(self.evaluators))
                    build_config["evaluator_name"]["value"] = self.current_evaluator

                # 注意：仅保留稳定字段，其余视为评估器动态配置。
                default_keys = ["code", "_type", "evaluator_name", "api_key", "input", "output", "timeout"]

                if field_value and field_value in self.evaluators and self.current_evaluator != field_value:
                    self.current_evaluator = field_value
                    evaluator = self.evaluators[field_value]

                    # 注意：切换评估器时必须移除旧动态字段，避免向后端发送不受支持的参数。
                    keys_to_remove = [key for key in build_config if key not in default_keys]
                    for key in keys_to_remove:
                        del build_config[key]

                    for attr in list(self.__dict__.keys()):
                        if attr not in default_keys and attr not in {
                            "evaluators",
                            "dynamic_inputs",
                            "_code",
                            "current_evaluator",
                        }:
                            delattr(self, attr)

                    self.dynamic_inputs = self.get_dynamic_inputs(evaluator)
                    for name, input_config in self.dynamic_inputs.items():
                        build_config[name] = input_config.to_dict()

                    # 注意：required 以评估器 schema 为准，避免 UI 与校验规则漂移。
                    required_fields = {"api_key", "evaluator_name"}.union(evaluator.get("requiredFields", []))
                    for key in build_config:
                        if isinstance(build_config[key], dict):
                            build_config[key]["required"] = key in required_fields

                missing_keys = [key for key in default_keys if key not in build_config]
                if missing_keys:
                    logger.warning(f"Missing required keys in build_config: {missing_keys}")
                    # 排障：补齐缺失键位以避免构建器在渲染阶段崩溃。
                    for key in missing_keys:
                        build_config[key] = {"value": None, "type": "str"}

            build_config["evaluator_name"]["value"] = self.current_evaluator

            logger.info(f"Current evaluator set to: {self.current_evaluator}")

        except (KeyError, AttributeError, ValueError) as e:
            self.status = f"Error updating component: {e!s}"
        return build_config

    def get_dynamic_inputs(self, evaluator: dict[str, Any]):
        """根据评估器 schema 生成动态输入配置

        契约：接收 `evaluator` 描述字典，返回输入名到 Input 实例的映射。副作用：无（失败时返回空 dict 并写 `self.status`）。
        关键路径（三步）：
        1) 合并 required/optional 字段并过滤 `input`/`output`
        2) 按字段类型创建输入组件
        3) 解析 `settings_json_schema` 生成高级配置
        异常流：schema 异常时返回 {}，调用方应回退到默认输入。
        排障入口：`Error creating dynamic inputs` 状态信息。
        决策：使用 schema 类型映射到输入控件而非手工配置
        问题：评估器设置项数量与类型不固定
        方案：解析 `settings_json_schema` 动态生成组件
        代价：schema 不规范会导致控件退化为文本输入
        重评：schema 稳定后可引入静态映射表
        """

        try:
            dynamic_inputs = {}

            input_fields = [
                field
                for field in evaluator.get("requiredFields", []) + evaluator.get("optionalFields", [])
                if field not in {"input", "output"}
            ]

            for field in input_fields:
                input_params = {
                    "name": field,
                    "display_name": field.replace("_", " ").title(),
                    "required": field in evaluator.get("requiredFields", []),
                }
                if field == "contexts":
                    dynamic_inputs[field] = MultilineInput(**input_params, multiline=True)
                else:
                    dynamic_inputs[field] = MessageTextInput(**input_params)

            settings = evaluator.get("settings", {})
            for setting_name, setting_config in settings.items():
                schema = evaluator.get("settings_json_schema", {}).get("properties", {}).get(setting_name, {})

                input_params = {
                    "name": setting_name,
                    "display_name": setting_name.replace("_", " ").title(),
                    "info": setting_config.get("description", ""),
                    "required": False,
                }

                if schema.get("type") == "object":
                    input_type = NestedDictInput
                    input_params["value"] = schema.get("default", setting_config.get("default", {}))
                elif schema.get("type") == "boolean":
                    input_type = BoolInput
                    input_params["value"] = schema.get("default", setting_config.get("default", False))
                elif schema.get("type") == "number":
                    is_float = isinstance(schema.get("default", setting_config.get("default")), float)
                    input_type = FloatInput if is_float else IntInput
                    input_params["value"] = schema.get("default", setting_config.get("default", 0))
                elif "enum" in schema:
                    input_type = DropdownInput
                    input_params["options"] = schema["enum"]
                    input_params["value"] = schema.get("default", setting_config.get("default"))
                else:
                    # 注意：无法识别类型时降级为文本输入，确保 UI 不被 schema 错误阻断。
                    input_type = MessageTextInput
                    default_value = schema.get("default", setting_config.get("default"))
                    input_params["value"] = str(default_value) if default_value is not None else ""

                dynamic_inputs[setting_name] = input_type(**input_params)

        except (KeyError, AttributeError, ValueError, TypeError) as e:
            self.status = f"Error creating dynamic inputs: {e!s}"
            return {}
        return dynamic_inputs

    async def evaluate(self) -> Data:
        """调用 LangWatch 评估接口并返回结果

        契约：需要 `api_key`；返回 `Data`，成功为评估 JSON，失败为 `{"error": ...}`。副作用：发起网络请求、更新 `self.status`。
        关键路径（三步）：
        1) 选择评估器并填充动态设置
        2) 组装 payload 并注入 `trace_id`
        3) 调用评估 API、解析结果
        异常流：请求失败/响应异常时返回错误文本，调用方可据此展示提示或重试。
        性能瓶颈：网络往返；受 `timeout`（默认 30s）控制。
        排障入口：状态前缀 `Evaluation error`，以及 `Evaluating with evaluator` 日志。
        决策：采用一次性 HTTP 调用而非流式评估
        问题：评估 API 以请求/响应形式提供
        方案：使用 `httpx.AsyncClient` 发送 JSON
        代价：无法实时流式返回结果
        重评：当 API 支持流式评估或需要长任务进度时
        """

        if not self.api_key:
            return Data(data={"error": "API key is required"})

        self.set_evaluators(os.getenv("LANGWATCH_ENDPOINT", "https://app.langwatch.ai"))
        self.dynamic_inputs = {}
        if getattr(self, "current_evaluator", None) is None and self.evaluators:
            self.current_evaluator = next(iter(self.evaluators))

        # Prioritize evaluator_name if it exists
        evaluator_name = getattr(self, "evaluator_name", None) or self.current_evaluator

        if not evaluator_name:
            if self.evaluators:
                evaluator_name = next(iter(self.evaluators))
                await logger.ainfo(f"No evaluator was selected. Using default: {evaluator_name}")
            else:
                return Data(
                    data={"error": "No evaluator selected and no evaluators available. Please choose an evaluator."}
                )

        try:
            evaluator = self.evaluators.get(evaluator_name)
            if not evaluator:
                return Data(data={"error": f"Selected evaluator '{evaluator_name}' not found."})

            await logger.ainfo(f"Evaluating with evaluator: {evaluator_name}")

            endpoint = f"/api/evaluations/{evaluator_name}/evaluate"
            url = f"{os.getenv('LANGWATCH_ENDPOINT', 'https://app.langwatch.ai')}{endpoint}"

            headers = {"Content-Type": "application/json", "X-Auth-Token": self.api_key}

            payload = {
                "data": {
                    "input": self.input,
                    "output": self.output,
                    "expected_output": self.expected_output,
                    "contexts": self.contexts.split(",") if self.contexts else [],
                },
                "settings": {},
            }

            if self._tracing_service:
                # 实现：注入 `trace_id` 以关联上游链路，便于跨系统排障。
                tracer = self._tracing_service.get_tracer("langwatch")
                if tracer is not None and hasattr(tracer, "trace_id"):
                    payload["settings"]["trace_id"] = str(tracer.trace_id)

            for setting_name in self.dynamic_inputs:
                payload["settings"][setting_name] = getattr(self, setting_name, None)

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)

            response.raise_for_status()
            result = response.json()

            formatted_result = json.dumps(result, indent=2)
            self.status = f"Evaluation completed successfully. Result:\n{formatted_result}"
            return Data(data=result)

        except (httpx.RequestError, KeyError, AttributeError, ValueError) as e:
            error_message = f"Evaluation error: {e!s}"
            self.status = error_message
            return Data(data={"error": error_message})
