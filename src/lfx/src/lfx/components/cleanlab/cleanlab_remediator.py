"""
模块名称：cleanlab_remediator

本模块提供 Cleanlab 可信度修复组件，用于基于分数进行响应治理。
主要功能包括：
- 功能1：根据阈值判断回复是否可信。
- 功能2：对不可信回复进行警告或替换。

使用场景：在评估组件之后对低可信度回复进行处置。
关键组件：
- 类 `CleanlabRemediator`

设计背景：将治理策略组件化，便于流程中统一接入。
注意事项：阈值与展示策略会直接影响用户可见结果。
"""

from lfx.custom import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import BoolInput, FloatInput, HandleInput, MessageTextInput, Output, PromptInput
from lfx.schema.message import Message


class CleanlabRemediator(Component):
    """基于可信度分数对回复进行治理的组件。

    契约：输入 `response/score/threshold` 等；输出 `remediated_response`。
    关键路径：
    1) 分数与阈值比较；
    2) 选择透传、警告追加或替换策略；
    3) 返回最终消息。
    异常流：分数缺失或类型异常由上游输入保证。
    排障入口：`self.status` 记录阈值判断与处置结果。
    决策：
    问题：低可信度回复需要统一治理策略。
    方案：基于阈值提供警告或替换两种模式。
    代价：可能隐藏部分有价值但低分的内容。
    重评：当引入更细粒度的治理策略或分级提示时。
    """

    display_name = "Cleanlab Remediator"
    description = (
        "Remediates an untrustworthy response based on trust score from the Cleanlab Evaluator, "
        "score threshold, and message handling settings."
    )
    icon = "Cleanlab"
    name = "CleanlabRemediator"

    inputs = [
        MessageTextInput(
            name="response",
            display_name="Response",
            info="The response to the user's query.",
            required=True,
        ),
        HandleInput(
            name="score",
            display_name="Trust Score",
            info="The trustworthiness score output from the Cleanlab Evaluator.",
            input_types=["number"],
            required=True,
        ),
        MessageTextInput(
            name="explanation",
            display_name="Explanation",
            info="The explanation from the Cleanlab Evaluator.",
            required=False,
        ),
        FloatInput(
            name="threshold",
            display_name="Threshold",
            field_type="float",
            value=0.7,
            range_spec=RangeSpec(min=0.0, max=1.0, step=0.05),
            info="Minimum score required to show the response unmodified. Reponses with scores above this threshold "
            "are considered trustworthy. Reponses with scores below this threshold are considered untrustworthy and "
            "will be remediated based on the settings below.",
            required=True,
            show=True,
        ),
        BoolInput(
            name="show_untrustworthy_response",
            display_name="Show Untrustworthy Response",
            info="If enabled, and the trust score is below the threshold, the original response is shown with the "
            "added warning. If disabled, and the trust score is below the threshold, the fallback answer is returned.",
            value=True,
        ),
        PromptInput(
            name="untrustworthy_warning_text",
            display_name="Warning for Untrustworthy Response",
            info="Warning to append to the response if Show Untrustworthy Response is enabled and trust score is "
            "below the threshold.",
            value="⚠️ WARNING: The following response is potentially untrustworthy.",
        ),
        PromptInput(
            name="fallback_text",
            display_name="Fallback Answer",
            info="Response returned if the trust score is below the threshold and 'Show Untrustworthy Response' is "
            "disabled.",
            value="Based on the available information, I cannot provide a complete answer to this question.",
        ),
    ]

    outputs = [
        Output(
            display_name="Remediated Message",
            name="remediated_response",
            method="remediate_response",
            types=["Message"],
        ),
    ]

    def remediate_response(self) -> Message:
        """根据可信度分数生成最终回复。

        契约：当分数 >= 阈值时透传；否则按配置警告或替换。
        关键路径：阈值比较 -> 分支构建消息 -> 返回。
        决策：
        问题：同一评分需要统一输出规则避免用户困惑。
        方案：阈值驱动的二分策略。
        代价：无法区分“稍低”和“极低”的细微差异。
        重评：当需要多级阈值与分级提示时。
        """
        if self.score >= self.threshold:
            self.status = f"Score {self.score:.2f} ≥ threshold {self.threshold:.2f} → accepted"
            return Message(
                text=f"{self.response}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n**Trust Score:** {self.score:.2f}"
            )

        self.status = f"Score {self.score:.2f} < threshold {self.threshold:.2f} → flagged"

        if self.show_untrustworthy_response:
            parts = [
                self.response,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"**{self.untrustworthy_warning_text.strip()}**",
                f"**Trust Score:** {self.score:.2f}",
            ]
            if self.explanation:
                parts.append(f"**Explanation:** {self.explanation}")
            return Message(text="\n\n".join(parts))

        return Message(text=self.fallback_text)
