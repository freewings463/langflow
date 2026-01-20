"""模块名称：WatsonX/Granite 工具调用适配

本模块提供 IBM WatsonX 平台模型的工具调用补偿逻辑，解决占位符参数与多工具调用限制问题。
主要功能包括：
- 识别 WatsonX/Granite 模型
- 增强系统提示以约束工具使用
- 检测占位符并触发纠正性重试
- 限制单次工具调用数

关键组件：
- `create_granite_agent`：构建具备动态 `tool_choice` 的代理
- `detect_placeholder_in_args`：占位符检测与告警
- `_limit_to_single_tool_call`：单工具调用限制

设计背景：WatsonX 平台在工具调用行为上与默认实现存在偏差。
注意事项：仅对 WatsonX 路径做补偿，不影响其他模型分支。
"""

import re

from langchain.agents.format_scratchpad.tools import format_to_tool_messages
from langchain.agents.output_parsers.tools import ToolsAgentOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from lfx.log.logger import logger

# 用于识别工具参数占位符的模式
PLACEHOLDER_PATTERN = re.compile(
    r"<[^>]*(?:result|value|output|response|data|from|extract|previous|current|date|input|query|search|tool)[^>]*>",
    re.IGNORECASE,
)


def is_watsonx_model(llm) -> bool:
    """判断给定 LLM 是否运行在 IBM WatsonX 平台。

    契约：输入 `llm`；输出 bool；副作用无；失败语义：无法读取属性时视为非 WatsonX。
    关键路径：1) 检查类名 2) 检查模块名。
    决策：优先基于类名判断
    问题：WatsonX 生态模型类名通常含 `watsonx`
    方案：对子串做不区分大小写匹配
    代价：可能误判包含同名子串的自定义类
    重评：当出现误判案例时改为显式 provider 标记
    """
    # 检查类名是否包含 WatsonX 标识（如 `ChatWatsonx`）
    class_name = type(llm).__name__.lower()
    if "watsonx" in class_name:
        return True

    # 回退检查模块名（如 `langchain_ibm`）
    module_name = getattr(type(llm), "__module__", "").lower()
    return "watsonx" in module_name or "langchain_ibm" in module_name


def is_granite_model(llm) -> bool:
    """判断是否 Granite 模型（已弃用）。

    契约：输入 `llm`；输出 bool；副作用无；失败语义：缺失字段时返回 False。
    关键路径：1) 读取 `model_id/model_name` 2) 子串匹配 `granite`。
    决策：保留函数作为兼容层
    问题：历史代码仍依赖 Granite 判断
    方案：提供兼容接口并提示迁移
    代价：新旧判断逻辑并存
    重评：当调用方全部迁移到 `is_watsonx_model` 后移除
    """
    model_id = getattr(llm, "model_id", getattr(llm, "model_name", ""))
    return "granite" in str(model_id).lower()


def _get_tool_schema_description(tool) -> str:
    """提取工具参数的简要描述。

    契约：输入 `tool`；输出参数描述字符串；副作用无；
    失败语义：解析失败时返回空串（降级而非中断）。
    关键路径：1) 读取 `args_schema` 2) 生成参数列表。
    决策：失败时返回空串
    问题：不同工具的 schema 结构不完全一致
    方案：捕获异常并降级
    代价：增强提示中缺失部分参数信息
    重评：当 schema 结构稳定后改为严格校验
    """
    if not hasattr(tool, "args_schema") or not tool.args_schema:
        return ""

    schema = tool.args_schema
    if not hasattr(schema, "model_fields"):
        return ""

    try:
        fields = schema.model_fields
        params = []
        for name, field in fields.items():
            required = field.is_required() if hasattr(field, "is_required") else True
            req_str = "(required)" if required else "(optional)"
            params.append(f"{name} {req_str}")
        return f"Parameters: {', '.join(params)}" if params else ""
    except (AttributeError, TypeError) as e:
        logger.debug(f"Could not extract schema for tool {getattr(tool, 'name', 'unknown')}: {e}")
        return ""


def get_enhanced_system_prompt(base_prompt: str, tools: list) -> str:
    """为 WatsonX 模型增强系统提示。

    契约：输入 `base_prompt/tools`；输出增强后的提示字符串；副作用无；
    失败语义：工具列表为空时返回原提示。
    关键路径：1) 组装工具描述 2) 拼接平台约束说明 3) 返回新提示。
    决策：仅在工具数量 > 1 时增强
    问题：单工具场景无需冗余说明
    方案：对长度做短路返回
    代价：单工具仍可能出现占位符问题
    重评：当单工具也频繁出错时改为全量增强
    """
    if not tools or len(tools) <= 1:
        return base_prompt

    # 构建包含参数说明的工具描述
    tool_descriptions = []
    for t in tools:
        schema_desc = _get_tool_schema_description(t)
        if schema_desc:
            tool_descriptions.append(f"- {t.name}: {schema_desc}")
        else:
            tool_descriptions.append(f"- {t.name}")

    tools_section = "\n".join(tool_descriptions)

    # 注意：一次只调用一个工具是 WatsonX 平台限制，并非设计偏好。
    # WatsonX 模型对并行工具调用支持不稳定。
    enhancement = f"""

TOOL USAGE GUIDELINES:

1. ALWAYS call tools when you need information - never say "I cannot" or "I don't have access".
2. Call one tool at a time, then use its result before calling another tool.
3. Use ACTUAL values in tool arguments - never use placeholder syntax like <result-from-...>.
4. Each tool has specific parameters - use the correct ones for each tool.

AVAILABLE TOOLS:
{tools_section}"""

    return base_prompt + enhancement


def detect_placeholder_in_args(tool_calls: list) -> tuple[bool, str | None]:
    """检测工具参数中是否包含占位符语法。

    契约：输入 `tool_calls` 列表；输出 `(是否命中, 命中文本)`；副作用：记录告警日志；
    失败语义：参数格式异常时视为未命中。
    关键路径：1) 遍历 tool_calls 2) 匹配正则 3) 告警并返回。
    决策：优先在参数级别打点告警
    问题：占位符会导致工具调用失败或无效
    方案：正则检测并记录 `tool.name` 与 `key`
    代价：日志可能包含用户输入片段
    重评：当引入脱敏日志时改为脱敏输出
    """
    if not tool_calls:
        return False, None

    for tool_call in tool_calls:
        args = tool_call.get("args", {})
        if isinstance(args, dict):
            for key, value in args.items():
                if isinstance(value, str) and PLACEHOLDER_PATTERN.search(value):
                    tool_name = tool_call.get("name", "unknown")
                    logger.warning(f"[IBM WatsonX] Detected placeholder: {tool_name}.{key}={value}")
                    return True, value
        elif isinstance(args, str) and PLACEHOLDER_PATTERN.search(args):
            logger.warning(f"[IBM WatsonX] Detected placeholder in args: {args}")
            return True, args
    return False, None


def _limit_to_single_tool_call(llm_response):
    """限制响应为单次工具调用（WatsonX 平台限制）。

    契约：输入 `llm_response`；输出同对象；副作用：可能原地裁剪 `tool_calls`；
    失败语义：缺少 `tool_calls` 属性时不做处理。
    关键路径：1) 检查 `tool_calls` 2) 超过 1 个时保留首个。
    决策：保留第一个工具调用
    问题：平台无法稳定处理多工具调用
    方案：只保留第一个以保证可执行
    代价：后续工具调用被丢弃
    重评：当平台支持并行工具调用时移除限制
    """
    if not hasattr(llm_response, "tool_calls") or not llm_response.tool_calls:
        return llm_response

    if len(llm_response.tool_calls) > 1:
        logger.debug(f"[WatsonX] Limiting {len(llm_response.tool_calls)} tool calls to 1")
        llm_response.tool_calls = [llm_response.tool_calls[0]]

    return llm_response


def _handle_placeholder_in_response(llm_response, messages, llm_auto):
    """当检测到占位符时触发纠正性重试。

    契约：输入 `llm_response/messages/llm_auto`；输出新的响应；
    副作用：可能追加 `SystemMessage` 并再次调用模型。
    失败语义：无占位符时返回原响应。
    关键路径：1) 检测占位符 2) 追加纠正消息 3) 复用 `llm_auto` 调用。
    决策：使用 `SystemMessage` 强制纠正
    问题：模型经常使用 `<result>` 等占位符
    方案：追加指令要求使用真实值
    代价：额外一次模型调用
    重评：当模型稳定输出真实值时移除此重试
    """
    if not hasattr(llm_response, "tool_calls") or not llm_response.tool_calls:
        return llm_response

    has_placeholder, _ = detect_placeholder_in_args(llm_response.tool_calls)
    if not has_placeholder:
        return llm_response

    logger.warning("[WatsonX] Placeholder detected, requesting actual values")
    from langchain_core.messages import SystemMessage

    corrective_msg = SystemMessage(
        content="Provide your final answer using the actual values from previous tool results."
    )
    messages_list = list(messages.messages) if hasattr(messages, "messages") else list(messages)
    messages_list.append(corrective_msg)
    return llm_auto.invoke(messages_list)


def create_granite_agent(llm, tools: list, prompt: ChatPromptTemplate, forced_iterations: int = 2):
    """构建适配 WatsonX/Granite 的工具调用代理。

    关键路径（三步）：
    1) 绑定 `tool_choice=required/auto` 的两个 LLM 分支
    2) 按迭代次数切换分支并执行
    3) 解析输出并附加 `ToolsAgentOutputParser`

    异常流：`llm` 不支持 `bind_tools` 时抛 `ValueError`。
    性能瓶颈：多次模型调用与工具调用串行化。
    排障入口：日志关键字 `[WatsonX]`。
    决策：前 N 轮强制 `required`，随后切换 `auto`
    问题：`auto` 时模型常描述工具不调用，`required` 又容易无法收敛
    方案：动态切换以兼顾工具执行与最终回答
    代价：额外控制逻辑与重试成本
    重评：当 WatsonX 工具调用稳定后改回单一策略
    """
    if not hasattr(llm, "bind_tools"):
        msg = "WatsonX handler requires a language model with bind_tools support."
        raise ValueError(msg)

    llm_required = llm.bind_tools(tools or [], tool_choice="required")
    llm_auto = llm.bind_tools(tools or [], tool_choice="auto")

    def invoke(inputs: dict):
        """执行一次代理调用并处理平台限制。

        契约：输入 `inputs` 字典；输出模型响应；副作用：记录调试日志；
        失败语义：上游异常透传。
        关键路径：1) 组装 scratchpad 与消息 2) 选择分支调用 3) 限制工具调用并纠正占位符。
        决策：以 `forced_iterations` 判定是否强制工具调用
        问题：过早允许 `auto` 会跳过工具
        方案：根据 `intermediate_steps` 计数切换
        代价：需要依赖调用次数状态
        重评：当模型可稳定调用工具时缩短或移除此计数
        """
        intermediate_steps = inputs.get("intermediate_steps", [])
        num_steps = len(intermediate_steps)

        scratchpad = format_to_tool_messages(intermediate_steps)
        messages = prompt.invoke({**inputs, "agent_scratchpad": scratchpad})

        # 前 N 轮使用 `required` 强制工具调用，之后切换为 `auto`
        use_required = num_steps < forced_iterations
        llm_to_use = llm_required if use_required else llm_auto
        logger.debug(f"[WatsonX] Step {num_steps + 1}, tool_choice={'required' if use_required else 'auto'}")

        response = llm_to_use.invoke(messages)
        response = _limit_to_single_tool_call(response)
        return _handle_placeholder_in_response(response, messages, llm_auto)

    return RunnableLambda(invoke) | ToolsAgentOutputParser()


# 兼容历史名称
create_watsonx_agent = create_granite_agent
