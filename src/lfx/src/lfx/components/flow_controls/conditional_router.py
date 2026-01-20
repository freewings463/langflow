"""
模块名称：条件路由组件（If-Else）

本模块提供基于文本比较的条件路由组件，主要用于根据比较结果
选择 True/False 分支输出，并在循环场景中控制分支执行。
主要功能包括：
- 支持多种比较操作符（含正则与数值比较）
- 在循环时限制最大迭代并回落到默认分支

关键组件：
- `ConditionalRouterComponent`：条件路由组件

设计背景：在流程控制中提供可视化的条件分支能力，并支持循环防护。
注意事项：数值比较失败会返回 False；正则非法时也返回 False。
"""

import re

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageInput, MessageTextInput, Output
from lfx.schema.message import Message


class ConditionalRouterComponent(Component):
    """基于文本比较的条件路由组件。

    契约：`true_response`/`false_response` 分别输出对应分支消息或空消息。
    副作用：更新上下文迭代计数并可能修改图的分支排除状态。
    失败语义：无显式异常抛出，非法比较将返回 False。
    """
    display_name = "If-Else"
    description = "Routes an input message to a corresponding output based on text comparison."
    documentation: str = "https://docs.langflow.org/if-else"
    icon = "split"
    name = "ConditionalRouter"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__iteration_updated = False

    inputs = [
        MessageTextInput(
            name="input_text",
            display_name="Text Input",
            info="The primary text input for the operation.",
            required=True,
        ),
        DropdownInput(
            name="operator",
            display_name="Operator",
            options=[
                "equals",
                "not equals",
                "contains",
                "starts with",
                "ends with",
                "regex",
                "less than",
                "less than or equal",
                "greater than",
                "greater than or equal",
            ],
            info="The operator to apply for comparing the texts.",
            value="equals",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="match_text",
            display_name="Match Text",
            info="The text input to compare against.",
            required=True,
        ),
        BoolInput(
            name="case_sensitive",
            display_name="Case Sensitive",
            info="If true, the comparison will be case sensitive.",
            value=True,
            advanced=True,
        ),
        MessageInput(
            name="true_case_message",
            display_name="Case True",
            info="The message to pass if the condition is True.",
            advanced=True,
        ),
        MessageInput(
            name="false_case_message",
            display_name="Case False",
            info="The message to pass if the condition is False.",
            advanced=True,
        ),
        IntInput(
            name="max_iterations",
            display_name="Max Iterations",
            info="The maximum number of iterations for the conditional router.",
            value=10,
            advanced=True,
        ),
        DropdownInput(
            name="default_route",
            display_name="Default Route",
            options=["true_result", "false_result"],
            info="The default route to take when max iterations are reached.",
            value="false_result",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="True", name="true_result", method="true_response", group_outputs=True),
        Output(display_name="False", name="false_result", method="false_response", group_outputs=True),
    ]

    def _pre_run_setup(self):
        """运行前重置迭代更新标记。"""
        self.__iteration_updated = False

    def evaluate_condition(self, input_text: str, match_text: str, operator: str, *, case_sensitive: bool) -> bool:
        """根据操作符比较输入文本并返回布尔结果。

        契约：返回 True 表示条件成立；不支持/错误比较返回 False。
        失败语义：正则非法或数值解析失败均返回 False。
        """
        if not case_sensitive and operator != "regex":
            input_text = input_text.lower()
            match_text = match_text.lower()

        if operator == "equals":
            return input_text == match_text
        if operator == "not equals":
            return input_text != match_text
        if operator == "contains":
            return match_text in input_text
        if operator == "starts with":
            return input_text.startswith(match_text)
        if operator == "ends with":
            return input_text.endswith(match_text)
        if operator == "regex":
            try:
                return bool(re.match(match_text, input_text))
            except re.error:
                return False  # 注意：正则非法时返回 False
        if operator in ["less than", "less than or equal", "greater than", "greater than or equal"]:
            try:
                input_num = float(input_text)
                match_num = float(match_text)
                if operator == "less than":
                    return input_num < match_num
                if operator == "less than or equal":
                    return input_num <= match_num
                if operator == "greater than":
                    return input_num > match_num
                if operator == "greater than or equal":
                    return input_num >= match_num
            except ValueError:
                return False  # 注意：数值解析失败时返回 False
        return False

    def iterate_and_stop_once(self, route_to_stop: str):
        """处理循环迭代计数与分支排除。

        关键路径（三步）：
        1) 更新迭代计数并读取当前次数
        2) 达到最大次数时切换为默认分支执行
        3) 常规路径下同时执行 stop 与条件排除
        异常流：无。
        排障入口：检查 `ctx` 中 `{_id}_iteration` 与 `conditional_exclusion_sources`。
        """
        if not self.__iteration_updated:
            self.update_ctx({f"{self._id}_iteration": self.ctx.get(f"{self._id}_iteration", 0) + 1})
            self.__iteration_updated = True
            current_iteration = self.ctx.get(f"{self._id}_iteration", 0)

            # 注意：达到最大次数且尝试停止默认分支时需要反向切换
            if current_iteration >= self.max_iterations and route_to_stop == self.default_route:
                # 实现：清理所有条件排除以允许默认分支执行
                if self._id in self.graph.conditional_exclusion_sources:
                    previous_exclusions = self.graph.conditional_exclusion_sources[self._id]
                    self.graph.conditionally_excluded_vertices -= previous_exclusions
                    del self.graph.conditional_exclusion_sources[self._id]

                # 实现：停止非默认分支以打破循环
                route_to_stop = "true_result" if route_to_stop == "false_result" else "false_result"

                # 实现：调用 stop 以中断循环
                self.stop(route_to_stop)
                # 注意：中断循环时不再执行条件排除
                return

            # 实现：常规路径同时使用两种机制
            # 1) stop() 管理循环状态（会在下一轮重置）
            self.stop(route_to_stop)

            # 2) 条件排除用于持久路由（仅由此路由器解除）
            self.graph.exclude_branch_conditionally(self._id, output_name=route_to_stop)

    def true_response(self) -> Message:
        """输出 True 分支结果或空消息。"""
        result = self.evaluate_condition(
            self.input_text, self.match_text, self.operator, case_sensitive=self.case_sensitive
        )

        # 注意：达到最大次数时强制走默认分支
        current_iteration = self.ctx.get(f"{self._id}_iteration", 0)
        force_output = current_iteration >= self.max_iterations and self.default_route == "true_result"

        if result or force_output:
            self.status = self.true_case_message
            if not force_output:  # 注意：仅在非强制情况下停止另一分支
                self.iterate_and_stop_once("false_result")
            return self.true_case_message
        self.iterate_and_stop_once("true_result")
        return Message(content="")

    def false_response(self) -> Message:
        """输出 False 分支结果或空消息。"""
        result = self.evaluate_condition(
            self.input_text, self.match_text, self.operator, case_sensitive=self.case_sensitive
        )

        if not result:
            self.status = self.false_case_message
            self.iterate_and_stop_once("true_result")
            return self.false_case_message

        self.iterate_and_stop_once("false_result")
        return Message(content="")

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """根据操作符动态调整构建配置。"""
        if field_name == "operator":
            if field_value == "regex":
                build_config.pop("case_sensitive", None)
            elif "case_sensitive" not in build_config:
                case_sensitive_input = next(
                    (input_field for input_field in self.inputs if input_field.name == "case_sensitive"), None
                )
                if case_sensitive_input:
                    build_config["case_sensitive"] = case_sensitive_input.to_dict()
        return build_config
