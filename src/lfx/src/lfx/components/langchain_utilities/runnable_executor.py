"""模块名称：Runnable 执行器组件

本模块封装对 `Runnable/AgentExecutor` 的统一执行入口，自动推断输入/输出键并支持流式输出。
主要功能包括：构建输入字典、解析输出键、选择流式或非流式执行。

关键组件：
- `RunnableExecComponent`：可执行 runnable 的组件化入口

设计背景：兼容不同 runnable 输出结构，减少手工适配成本。
注意事项：仅支持 `AgentExecutor` 类型，非该类型会抛 `TypeError`。
"""

from langchain.agents import AgentExecutor

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, HandleInput, MessageTextInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class RunnableExecComponent(Component):
    """Runnable 执行器组件。

    契约：输入 `runnable/input_value/input_key/output_key/use_stream`；输出 `Message` 或流式片段；
    副作用：更新 `self.status`；失败语义：`runnable` 非 `AgentExecutor` 抛 `TypeError`。
    关键路径：1) 构建输入字典 2) 执行 runnable 3) 解析输出并更新状态。
    决策：仅允许 `AgentExecutor`
    问题：非 executor 的输入/输出协议不稳定
    方案：在入口做类型校验
    代价：降低通用性
    重评：当协议稳定或可配置时放开类型限制
    """
    description = "Execute a runnable. It will try to guess the input and output keys."
    display_name = "Runnable Executor"
    name = "RunnableExecutor"
    beta: bool = True
    icon = "LangChain"

    inputs = [
        MessageTextInput(name="input_value", display_name="Input", required=True),
        HandleInput(
            name="runnable",
            display_name="Agent Executor",
            input_types=["Chain", "AgentExecutor", "Agent", "Runnable"],
            required=True,
        ),
        MessageTextInput(
            name="input_key",
            display_name="Input Key",
            value="input",
            advanced=True,
        ),
        MessageTextInput(
            name="output_key",
            display_name="Output Key",
            value="output",
            advanced=True,
        ),
        BoolInput(
            name="use_stream",
            display_name="Stream",
            value=False,
        ),
    ]

    outputs = [
        Output(
            display_name="Message",
            name="text",
            method="build_executor",
        ),
    ]

    def get_output(self, result, input_key, output_key):
        """从结果字典中推断输出值。

        契约：输入 `result/input_key/output_key`；输出 `(result_value, status)`；
        副作用：无；失败语义：未知键时回退为原始结果。
        关键路径：1) 命中显式 `output_key` 2) 回退常见输出键 3) 生成提示状态。
        决策：使用候选键列表兜底
        问题：不同 agent 输出键名不一致
        方案：按优先级尝试 `answer/response/output/result/text`
        代价：可能命中非预期键
        重评：当执行器提供显式输出规范时移除兜底
        """
        possible_output_keys = ["answer", "response", "output", "result", "text"]
        status = ""
        result_value = None

        if output_key in result:
            result_value = result.get(output_key)
        elif len(result) == 2 and input_key in result:  # noqa: PLR2004
            # 从结果中获取另一个键
            other_key = next(k for k in result if k != input_key)
            if other_key == output_key:
                result_value = result.get(output_key)
            else:
                status += f"Warning: The output key is not '{output_key}'. The output key is '{other_key}'."
                result_value = result.get(other_key)
        elif len(result) == 1:
            result_value = next(iter(result.values()))
        elif any(k in result for k in possible_output_keys):
            for key in possible_output_keys:
                if key in result:
                    result_value = result.get(key)
                    status += f"Output key: '{key}'."
                    break
            if result_value is None:
                result_value = result
                status += f"Warning: The output key is not '{output_key}'."
        else:
            result_value = result
            status += f"Warning: The output key is not '{output_key}'."

        return result_value, status

    def get_input_dict(self, runnable, input_key, input_value):
        """根据 runnable 的输入键构造入参字典。

        契约：输入 `runnable/input_key/input_value`；输出 `(input_dict, status)`；
        副作用：无；失败语义：无 `input_keys` 时返回空字典。
        关键路径：1) 检查 `input_keys` 2) 命中则使用指定键 3) 否则填充全部键。
        决策：无法匹配时广播输入值
        问题：不同 runnable 需要不同输入键
        方案：使用 `dict.fromkeys` 广播同一输入
        代价：可能覆盖其他字段期望
        重评：当支持多输入配置时改为显式映射
        """
        input_dict = {}
        status = ""
        if hasattr(runnable, "input_keys"):
            # 检查输入键是否在 runnable 的 `input_keys` 中
            if input_key in runnable.input_keys:
                input_dict[input_key] = input_value
            else:
                input_dict = dict.fromkeys(runnable.input_keys, input_value)
                status = f"Warning: The input key is not '{input_key}'. The input key is '{runnable.input_keys}'."
        return input_dict, status

    async def build_executor(self) -> Message:
        """执行 runnable 并返回结果或流式输出。

        关键路径（三步）：
        1) 构造输入字典并校验类型
        2) 根据 `use_stream` 选择执行路径
        3) 解析输出并写入状态

        异常流：类型不匹配抛 `TypeError`；执行异常透传。
        排障入口：`self.status` 包含输出键提示与原始结果。
        决策：优先使用 `ainvoke` 异步执行
        问题：需要兼容异步链路
        方案：统一走异步接口
        代价：同步调用方需要等待事件循环
        重评：当支持同步执行路径时增加分支
        """
        input_dict, status = self.get_input_dict(self.runnable, self.input_key, self.input_value)
        if not isinstance(self.runnable, AgentExecutor):
            msg = "The runnable must be an AgentExecutor"
            raise TypeError(msg)

        if self.use_stream:
            return self.astream_events(input_dict)
        result = await self.runnable.ainvoke(input_dict)
        result_value, status_ = self.get_output(result, self.input_key, self.output_key)
        status += status_
        status += f"\n\nOutput: {result_value}\n\nRaw Output: {result}"
        self.status = status
        return result_value

    async def astream_events(self, runnable_input):
        """流式输出 runnable 事件中的聊天增量。

        契约：输入 runnable_input；输出异步迭代的 chunk；副作用无；
        失败语义：事件格式异常时可能返回 `None`。
        关键路径：1) 订阅 `astream_events` 2) 过滤事件类型 3) 产出 chunk。
        决策：仅消费 `on_chat_model_stream`
        问题：其他事件类型与文本输出无关
        方案：按 `event` 类型过滤
        代价：丢弃工具/检索等事件
        重评：当需要完整事件流时改为透传
        """
        async for event in self.runnable.astream_events(runnable_input, version="v1"):
            if event.get("event") != "on_chat_model_stream":
                continue

            yield event.get("data").get("chunk")
