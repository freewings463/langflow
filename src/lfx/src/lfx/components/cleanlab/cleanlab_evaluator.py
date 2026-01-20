"""
模块名称：cleanlab_evaluator

本模块提供 Cleanlab 可信度评估组件，用于衡量 LLM 输出可靠性并生成解释。
主要功能包括：
- 功能1：基于 Cleanlab TLM 计算 trustworthiness 分数。
- 功能2：输出评分解释并将原始回复透传给下游。

使用场景：对 LLM 回复进行可信度评估并作为流程 gating 信号。
关键组件：
- 类 `CleanlabEvaluator`

设计背景：将 Cleanlab 评估逻辑封装为组件，避免在流程中手写调用。
注意事项：需要有效 API Key；模型与质量预设会影响成本与延迟。
"""

from cleanlab_tlm import TLM

from lfx.custom import Component
from lfx.io import (
    DropdownInput,
    MessageTextInput,
    Output,
    SecretStrInput,
)
from lfx.schema.message import Message


class CleanlabEvaluator(Component):
    """基于 Cleanlab 的 LLM 可信度评估组件。

    契约：输入包含 `prompt/response/api_key`；输出 `score` 与 `explanation`，并透传原始 `response`。
    关键路径：
    1) 组合 `system_prompt + prompt`；
    2) 使用 TLM 计算可信度分数；
    3) 输出分数、解释与原始回复。
    异常流：API Key 缺失或网络错误将由 Cleanlab SDK 抛出。
    排障入口：可查看 `self.status` 中的评分与流程状态。
    决策：
    问题：流程中缺少统一的可信度评估与解释输出。
    方案：封装为组件并缓存一次评估结果。
    代价：评估需额外调用外部 API，增加成本与延迟。
    重评：当引入本地评估模型或批量评估接口时。
    """

    display_name = "Cleanlab Evaluator"
    description = "Evaluates any LLM response using Cleanlab and outputs trust score and explanation."
    icon = "Cleanlab"
    name = "CleanlabEvaluator"

    inputs = [
        MessageTextInput(
            name="system_prompt",
            display_name="System Message",
            info="System-level instructions prepended to the user query.",
            value="",
        ),
        MessageTextInput(
            name="prompt",
            display_name="Prompt",
            info="The user's query to the model.",
            required=True,
        ),
        MessageTextInput(
            name="response",
            display_name="Response",
            info="The response to the user's query.",
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Cleanlab API Key",
            info="Your Cleanlab API key.",
            required=True,
        ),
        DropdownInput(
            name="model",
            display_name="Cleanlab Evaluation Model",
            options=[
                "gpt-4.1",
                "gpt-4.1-mini",
                "gpt-4.1-nano",
                "o4-mini",
                "o3",
                "gpt-4.5-preview",
                "gpt-4o-mini",
                "gpt-4o",
                "o3-mini",
                "o1",
                "o1-mini",
                "gpt-4",
                "gpt-3.5-turbo-16k",
                "claude-3.7-sonnet",
                "claude-3.5-sonnet-v2",
                "claude-3.5-sonnet",
                "claude-3.5-haiku",
                "claude-3-haiku",
                "nova-micro",
                "nova-lite",
                "nova-pro",
            ],
            info="The model Cleanlab uses to evaluate the response. This does NOT need to be the same model that "
            "generated the response.",
            value="gpt-4o-mini",
            required=True,
            advanced=True,
        ),
        DropdownInput(
            name="quality_preset",
            display_name="Quality Preset",
            options=["base", "low", "medium", "high", "best"],
            value="medium",
            info="This determines the accuracy, latency, and cost of the evaluation. Higher quality is generally "
            "slower but more accurate.",
            required=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            display_name="Response",
            name="response_passthrough",
            method="pass_response",
            types=["Message"],
        ),
        Output(display_name="Trust Score", name="score", method="get_score", types=["number"]),
        Output(
            display_name="Explanation",
            name="explanation",
            method="get_explanation",
            types=["Message"],
        ),
    ]

    def _evaluate_once(self):
        """执行一次评估并缓存结果。

        契约：返回 Cleanlab 评估结果字典；同一实例内重复调用复用缓存。
        关键路径：构建 full_prompt -> 初始化 TLM -> 调用 `get_trustworthiness_score`。
        异常流：网络/API 失败会抛异常，由调用方处理。
        决策：
        问题：多次获取分数与解释会重复调用外部 API。
        方案：使用实例级 `_cached_result` 缓存。
        代价：同一实例内不会反映输入变更后的结果。
        重评：当需要实时刷新评估或支持批量评估时。
        """
        if not hasattr(self, "_cached_result"):
            full_prompt = f"{self.system_prompt}\n\n{self.prompt}" if self.system_prompt else self.prompt
            tlm = TLM(
                api_key=self.api_key,
                options={"log": ["explanation"], "model": self.model},
                quality_preset=self.quality_preset,
            )
            self._cached_result = tlm.get_trustworthiness_score(full_prompt, self.response)
        return self._cached_result

    def get_score(self) -> float:
        """返回可信度分数（0-1）。

        契约：返回浮点分数；同时更新 `status`。
        关键路径：调用 `_evaluate_once` -> 读取 `trustworthiness_score`。
        异常流：评估失败时异常向上抛出。
        决策：
        问题：下游需要数值型可信度用于阈值判断。
        方案：单独输出分数并更新状态。
        代价：无额外代价，依赖缓存结果。
        重评：当需要输出更多统计指标时。
        """
        result = self._evaluate_once()
        score = result.get("trustworthiness_score", 0.0)
        self.status = f"Trust score: {score:.2f}"
        return score

    def get_explanation(self) -> Message:
        """返回可信度解释文本。

        契约：返回 `Message(text=explanation)`；缺省返回占位文本。
        关键路径：调用 `_evaluate_once` -> 读取 `log.explanation`。
        异常流：评估失败时异常向上抛出。
        决策：
        问题：仅有分数不足以指导用户理解问题原因。
        方案：输出 Cleanlab 解释文本。
        代价：解释文本长度不可控，可能较长。
        重评：当需要结构化解释或多语言支持时。
        """
        result = self._evaluate_once()
        explanation = result.get("log", {}).get("explanation", "No explanation returned.")
        return Message(text=explanation)

    def pass_response(self) -> Message:
        """透传原始回复，便于下游继续使用。

        契约：返回 `Message(text=response)`；不修改内容。
        关键路径：更新状态 -> 返回消息。
        决策：
        问题：评估后仍需保留原始回复供下游处理。
        方案：提供透传输出端口。
        代价：无额外代价。
        重评：当需要附加元信息或标注可信度时。
        """
        self.status = "Passing through response."
        return Message(text=self.response)
