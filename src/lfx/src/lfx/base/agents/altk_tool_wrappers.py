"""
模块名称：`ALTK` 工具包装器与校验适配

本模块提供 `ALTK` 工具包装器实现与 `SPARC` 验证包装逻辑，主要用于在代理调用工具前进行
参数校验、上下文记录与错误恢复。
主要功能包括：
- `Pydantic` 参数到 `OpenAI` `schema` 的转换
- `SPARC` 反思验证与拒绝消息构建
- 包装器链路的创建、嵌套与解包

关键组件：
- `ValidatedTool`：带 SPARC 验证的工具包装器
- `PreToolValidationWrapper`：执行前验证包装器
- `PostToolProcessor`：执行后处理代理工具
- `PostToolProcessingWrapper`：执行后处理包装器

设计背景：工具协议与校验逻辑分散，需要集中适配与复用。
注意事项：包装器嵌套深度受 `_MAX_WRAPPER_DEPTH` 限制；验证失败会回退到直接执行。
"""

# 决策：最大包装器嵌套深度
# 问题：防止无限循环
# 方案：设置最大包装器嵌套深度限制
# 代价：限制了嵌套层数
# 重评：当需要更深的嵌套时重新评估
_MAX_WRAPPER_DEPTH = 10


def _convert_pydantic_type_to_json_schema_type(param_info: dict) -> dict:
    """将 Pydantic 参数信息转换为 OpenAI 函数调用 JSON 模式格式

    关键路径（三步）：
    1) 处理简单类型（字符串、数字、整数、布尔值等）
    2) 处理复杂类型（anyOf、oneOf、allOf 等联合类型）
    3) 返回兼容 OpenAI 函数调用格式的字典

    异常流：无法确定类型时返回字符串类型的默认值。
    性能瓶颈：递归处理复杂类型时。
    排障入口：日志关键字 "Could not determine type for param_info"。
    
    契约：
    - 输入：来自 LangChain 工具参数的信息字典
    - 输出：与 OpenAI 函数调用格式兼容的类型字典
    - 副作用：无
    - 失败语义：如果无法确定类型，则返回默认字符串类型
    """
    # 先处理简单类型
    if "type" in param_info:
        schema_type = param_info["type"]

        # 直接类型映射
        if schema_type in ("string", "number", "integer", "boolean", "null", "object"):
            return {
                "type": schema_type,
                "description": param_info.get("description", ""),
            }

        # 数组类型
        if schema_type == "array":
            result = {"type": "array", "description": param_info.get("description", "")}
            # 若存在 `items` 则补充其 `schema`
            if "items" in param_info:
                items_schema = _convert_pydantic_type_to_json_schema_type(param_info["items"])
                result["items"] = items_schema
            return result

    # 处理 `anyOf` 的联合类型（如 `list[str] | None`）
    if "anyOf" in param_info:
        # 找到最具体的非空类型
        for variant in param_info["anyOf"]:
            if variant.get("type") == "null":
                continue  # Skip null variants

            # 处理该非空变体
            converted = _convert_pydantic_type_to_json_schema_type(variant)
            converted["description"] = param_info.get("description", "")

            # 存在默认值时视为可选
            if "default" in param_info:
                converted["default"] = param_info["default"]

            return converted

    # 处理 `oneOf`（类似 `anyOf`）
    if "oneOf" in param_info:
        # 取第一个非空选项
        for variant in param_info["oneOf"]:
            if variant.get("type") != "null":
                converted = _convert_pydantic_type_to_json_schema_type(variant)
                converted["description"] = param_info.get("description", "")
                return converted

    # 处理 `allOf`（交集类型）
    if param_info.get("allOf"):
        # 暂时取第一个 `schema`
        converted = _convert_pydantic_type_to_json_schema_type(param_info["allOf"][0])
        converted["description"] = param_info.get("description", "")
        return converted

    # 兜底：尝试从 `title` 推断，否则默认字符串
    logger.debug(f"Could not determine type for param_info: {param_info}")
    return {
        "type": "string",  # Safe fallback
        "description": param_info.get("description", ""),
    }


class ValidatedTool(ALTKBaseTool):
    """使用 SPARC 反思在执行前验证调用的包装工具

    如果 SPARC 不可用，则退回到简单验证。
    
    关键路径（三步）：
    1) 准备工具调用以进行 SPARC 验证
    2) 运行 SPARC 验证过程
    3) 根据验证结果执行或拒绝工具调用
    
    异常流：SPARC 验证失败时直接执行工具。
    性能瓶颈：SPARC 验证过程。
    排障入口：日志关键字 "SPARC validation result"、"SPARC rejected tool call"。
    
    契约：
    - 输入：被包装的工具、代理和其他参数
    - 输出：ValidatedTool 实例
    - 副作用：初始化 SPARC 反思组件
    - 失败语义：如果验证失败，则返回格式化的拒绝消息
    """

    sparc_component: Any | None = Field(default=None)
    conversation_context: list[BaseMessage] = Field(default_factory=list)
    tool_specs: list[dict] = Field(default_factory=list)
    validation_attempts: dict[str, int] = Field(default_factory=dict)
    current_conversation_context: list[BaseMessage] = Field(default_factory=list)
    previous_tool_calls_in_current_step: list[dict] = Field(default_factory=list)
    previous_reflection_messages: dict[str, str] = Field(default_factory=list)

    def __init__(
        self,
        wrapped_tool: BaseTool,
        agent,
        sparc_component=None,
        conversation_context=None,
        tool_specs=None,
        **kwargs,
    ):
        """初始化验证工具

        契约：
        - 输入：被包装的工具、代理和其他参数
        - 输出：ValidatedTool 实例
        - 副作用：初始化父类和所有字段
        - 失败语义：如果初始化失败，抛出相应异常
        """
        super().__init__(
            name=wrapped_tool.name,
            description=wrapped_tool.description,
            wrapped_tool=wrapped_tool,
            sparc_component=sparc_component,
            conversation_context=conversation_context or [],
            tool_specs=tool_specs or [],
            agent=agent,
            **kwargs,
        )

    def _run(self, *args, **kwargs) -> str:
        """执行带验证的工具

        契约：
        - 输入：位置参数和关键字参数
        - 输出：工具执行结果字符串
        - 副作用：初始化 SPARC 反思组件
        - 失败语义：如果执行失败，抛出相应异常
        """
        self.sparc_component = SPARCReflectionComponent(
            config=ComponentConfig(llm_client=self._get_altk_llm_object()),
            track=Track.FAST_TRACK,  # Use fast track for performance
            execution_mode=SPARCExecutionMode.SYNC,  # Use SYNC to avoid event loop conflicts
        )
        return self._validate_and_run(*args, **kwargs)

    @staticmethod
    def _custom_message_to_dict(message: BaseMessage) -> dict:
        """将 BaseMessage 转换为字典

        契约：
        - 输入：BaseMessage 对象
        - 输出：字典表示的消息
        - 副作用：无
        - 失败语义：如果消息类型无效，抛出 ValueError
        """
        if isinstance(message, BaseMessage):
            return message_to_dict(message)
        msg = f"Invalid message type: {type(message)}"
        logger.error(msg, exc_info=True)
        raise ValueError(msg) from None

    def _validate_and_run(self, *args, **kwargs) -> str:
        """使用 SPARC 验证工具调用并执行（如果有效）

        关键路径（三步）：
        1) 准备工具调用以进行 SPARC 验证
        2) 运行 SPARC 验证过程
        3) 根据验证结果执行工具或返回拒绝消息

        异常流：SPARC 验证过程中的各种异常。
        性能瓶颈：SPARC 验证过程。
        排障入口：日志关键字 "SPARC validation result"、"Error during SPARC validation"。
        
        契约：
        - 输入：位置参数和关键字参数
        - 输出：验证结果或工具执行结果
        - 副作用：更新会话上下文和工具调用记录
        - 失败语义：如果验证失败，返回格式化的拒绝消息；如果执行失败，抛出相应异常
        """
        # 判断是否绕过验证
        if not self.sparc_component:
            return self._execute_tool(*args, **kwargs)

        # 准备 `SPARC` 验证所需的工具调用
        tool_call = {
            "id": str(uuid.uuid4()),
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self._prepare_arguments(*args, **kwargs)),
            },
        }

        if (
            isinstance(self.conversation_context, list)
            and self.conversation_context
            and isinstance(self.conversation_context[0], BaseMessage)
        ):
            logger.debug("Converting BaseMessages to list of dictionaries for conversation context of SPARC")
            self.conversation_context = [self._custom_message_to_dict(msg) for msg in self.conversation_context]

        logger.debug(
            f"Converted conversation context for SPARC for tool call:\n"
            f"{json.dumps(tool_call, indent=2)}\n{self.conversation_context=}"
        )

        try:
            # 执行 `SPARC` 验证
            run_input = SPARCReflectionRunInput(
                messages=self.conversation_context + self.previous_tool_calls_in_current_step,
                tool_specs=self.tool_specs,
                tool_calls=[tool_call],
            )

            if self.current_conversation_context != self.conversation_context:
                logger.info("Updating conversation context for SPARC validation")
                self.current_conversation_context = self.conversation_context
                self.previous_tool_calls_in_current_step = []
            else:
                logger.info("Using existing conversation context for SPARC validation")
                self.previous_tool_calls_in_current_step.append(tool_call)

            # 工具规格缺失时可选择绕过
            if not self.tool_specs:
                logger.warning(f"No tool specs available for SPARC validation of {self.name}, executing directly")
                return self._execute_tool(*args, **kwargs)

            result = self.sparc_component.process(run_input, phase=AgentPhase.RUNTIME)
            logger.debug(f"SPARC validation result for tool {self.name}: {result.output.reflection_result}")

            # 检查验证结果
            if result.output.reflection_result.decision.name == "APPROVE":
                logger.info(f"✅ SPARC approved tool call for {self.name}")
                return self._execute_tool(*args, **kwargs)
            logger.info(f"❌ SPARC rejected tool call for {self.name}")
            return self._format_sparc_rejection(result.output.reflection_result)

        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error during SPARC validation: {e}")
            # 验证出错时直接执行
            return self._execute_tool(*args, **kwargs)

    def _prepare_arguments(self, *args, **kwargs) -> dict[str, Any]:
        """为 SPARC 验证准备参数

        契约：
        - 输入：位置参数和关键字参数
        - 输出：格式化的参数字典
        - 副作用：移除不需要的配置参数
        - 失败语义：如果参数准备失败，返回清理后的关键字参数
        """
        # 若包含 `config` 参数则移除（验证不需要）
        clean_kwargs = {k: v for k, v in kwargs.items() if k != "config"}

        # 若有位置参数，尝试映射到参数名
        if args and hasattr(self.wrapped_tool, "args_schema"):
            try:
                schema = self.wrapped_tool.args_schema
                field_source = None
                if hasattr(schema, "__fields__"):
                    field_source = schema.__fields__
                elif hasattr(schema, "model_fields"):
                    field_source = schema.model_fields
                if field_source:
                    field_names = list(field_source.keys())
                    for i, arg in enumerate(args):
                        if i < len(field_names):
                            clean_kwargs[field_names[i]] = arg
            except (AttributeError, KeyError, TypeError):
                # `schema` 解析失败则直接使用 `kwargs`
                pass

        return clean_kwargs

    def _format_sparc_rejection(self, reflection_result) -> str:
        """将 SPARC 拒绝格式化为有用的错误消息

        契约：
        - 输入：反思结果对象
        - 输出：格式化的错误消息字符串
        - 副作用：无
        - 失败语义：如果没有问题记录，返回通用错误消息
        """
        if not reflection_result.issues:
            return "Error: Tool call validation failed - please review your approach and try again"

        error_parts = ["Tool call validation failed:"]

        for issue in reflection_result.issues:
            error_parts.append(f"\n• {issue.explanation}")
            if issue.correction:
                try:
                    correction_data = issue.correction
                    if isinstance(correction_data, dict):
                        if "corrected_function_name" in correction_data:
                            error_parts.append(f"  💡 Suggested function: {correction_data['corrected_function_name']}")
                        elif "tool_call" in correction_data:
                            suggested_args = correction_data["tool_call"].get("arguments", {})
                            error_parts.append(f"  💡 Suggested parameters: {suggested_args}")
                except (AttributeError, KeyError, TypeError):
                    # 校正解析失败则跳过
                    pass

        error_parts.append("\nPlease adjust your approach and try again.")
        return "\n".join(error_parts)

    def update_context(self, conversation_context: list[BaseMessage]):
        """更新对话上下文

        契约：
        - 输入：BaseMessage 对象列表
        - 输出：无
        - 副作用：更新 conversation_context 字段
        - 失败语义：无
        """
        self.conversation_context = conversation_context


class PreToolValidationWrapper(BaseToolWrapper):
    """添加预工具验证功能的工具包装器

    此包装器在执行前使用 SPARC 反思组件验证工具调用的适当性和正确性。
    
    契约：
    - 输入：BaseTool 对象及额外参数
    - 输出：带有验证功能的包装工具
    - 副作用：初始化 SPARC 验证组件
    - 失败语义：如果包装失败，返回原始工具
    """

    def __init__(self):
        """初始化预工具验证包装器

        契约：
        - 输入：无
        - 输出：PreToolValidationWrapper 实例
        - 副作用：初始化工具规格列表
        - 失败语义：无
        """
        self.tool_specs = []

    def wrap_tool(self, tool: BaseTool, **kwargs) -> BaseTool:
        """使用验证功能包装工具

        关键路径（三步）：
        1) 检查工具是否已被包装
        2) 验证必要参数是否存在
        3) 应用验证包装器

        异常流：缺少代理参数时返回原始工具。
        性能瓶颈：无显著性能瓶颈。
        排障入口：日志关键字 "Cannot wrap tool with PreToolValidationWrapper"。
        
        契约：
        - 输入：BaseTool 对象和关键字参数
        - 输出：包装后的 BaseTool 对象
        - 副作用：可能更新现有验证工具的上下文
        - 失败语义：如果代理参数缺失，返回原始工具
        """
        if isinstance(tool, ValidatedTool):
            # 已包装则仅更新上下文与工具规格
            tool.tool_specs = self.tool_specs
            if "conversation_context" in kwargs:
                tool.update_context(kwargs["conversation_context"])
            logger.debug(f"Updated existing ValidatedTool {tool.name} with {len(self.tool_specs)} tool specs")
            return tool

        agent = kwargs.get("agent")

        if not agent:
            logger.warning("Cannot wrap tool with PreToolValidationWrapper: missing 'agent'")
            return tool

        # 使用验证包装器包裹
        return ValidatedTool(
            wrapped_tool=tool,
            agent=agent,
            tool_specs=self.tool_specs,
            conversation_context=kwargs.get("conversation_context", []),
        )

    @staticmethod
    def convert_langchain_tools_to_sparc_tool_specs_format(
        tools: list[BaseTool],
    ) -> list[dict]:
        """将 LangChain 工具转换为 SPARC 验证的 OpenAI 函数调用格式

        关键路径（三步）：
        1) 遍历 LangChain 工具列表
        2) 为每个工具构建 OpenAI 函数调用格式的规格
        3) 提取参数并转换为 JSON 模式格式

        异常流：工具转换失败时创建最小规格。
        性能瓶颈：递归处理复杂参数类型时。
        排障入口：日志关键字 "Could not convert tool"、"No tool specs were generated"。
        
        契约：
        - 输入：LangChain BaseTool 实例列表
        - 输出：OpenAI 函数调用格式的工具规格列表
        - 副作用：无
        - 失败语义：如果无法生成任何规格，记录错误日志
        """
        tool_specs = []

        for i, tool in enumerate(tools):
            try:
                # 处理嵌套包装器
                unwrapped_tool = tool
                wrapper_count = 0

                # 解包直到真实工具
                while hasattr(unwrapped_tool, "wrapped_tool") and not isinstance(unwrapped_tool, ValidatedTool):
                    unwrapped_tool = unwrapped_tool.wrapped_tool
                    wrapper_count += 1
                    if wrapper_count > _MAX_WRAPPER_DEPTH:  # 注意：防止无限循环
                        break

                # 从 `LangChain` 工具构建规格
                tool_spec = {
                    "type": "function",
                    "function": {
                        "name": unwrapped_tool.name,
                        "description": unwrapped_tool.description or f"Tool: {unwrapped_tool.name}",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                }

                # 若可用则从 `schema` 提取参数
                args_dict = unwrapped_tool.args
                if isinstance(args_dict, dict):
                    for param_name, param_info in args_dict.items():
                        logger.debug(f"Processing parameter: {param_name}")
                        logger.debug(f"Parameter info: {param_info}")

                        # 使用新的转换函数
                        param_spec = _convert_pydantic_type_to_json_schema_type(param_info)

                        # 通过 `Pydantic` 字段判断参数是否必填
                        if unwrapped_tool.args_schema and hasattr(unwrapped_tool.args_schema, "model_fields"):
                            field_info = unwrapped_tool.args_schema.model_fields.get(param_name)
                            if field_info and field_info.is_required():
                                tool_spec["function"]["parameters"]["required"].append(param_name)

                        tool_spec["function"]["parameters"]["properties"][param_name] = param_spec

                tool_specs.append(tool_spec)

            except (AttributeError, KeyError, TypeError, ValueError) as e:
                logger.warning(f"Could not convert tool {getattr(tool, 'name', 'unknown')} to spec: {e}")
                # 创建最小规格
                minimal_spec = {
                    "type": "function",
                    "function": {
                        "name": getattr(tool, "name", f"unknown_tool_{i}"),
                        "description": getattr(
                            tool,
                            "description",
                            f"Tool: {getattr(tool, 'name', 'unknown')}",
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                }
                tool_specs.append(minimal_spec)

        if not tool_specs:
            logger.error("⚠️ No tool specs were generated! This will cause SPARC validation to fail")
        return tool_specs


class PostToolProcessor(ALTKBaseTool):
    """处理工具输出的工具输出处理器

    此包装器拦截工具执行输出，如果工具输出是 JSON，
    它会调用 ALTK 组件通过生成 Python 代码从 JSON 中提取信息。
    
    关键路径（三步）：
    1) 执行被包装的工具
    2) 检查输出是否为 JSON 格式
    3) 如果是大 JSON，则使用代码生成组件处理
    
    异常流：后处理失败时返回原始结果。
    性能瓶颈：代码生成组件执行时。
    排障入口：日志关键字 "Error in post-processing tool response"、"Exception in executing CodeGenerationComponent"。
    
    契约：
    - 输入：被包装的工具、用户查询、代理和其他参数
    - 输出：PostToolProcessor 实例
    - 副作用：继承自 ALTKBaseTool 的功能
    - 失败语义：如果后处理失败，返回原始工具结果
    """

    user_query: str = Field(...)
    response_processing_size_threshold: int = Field(...)

    def __init__(
        self,
        wrapped_tool: BaseTool,
        user_query: str,
        agent,
        response_processing_size_threshold: int,
        **kwargs,
    ):
        """初始化后工具处理器

        契约：
        - 输入：被包装的工具、用户查询、代理等参数
        - 输出：PostToolProcessor 实例
        - 副作用：初始化父类和所有字段
        - 失败语义：如果初始化失败，抛出相应异常
        """
        super().__init__(
            name=wrapped_tool.name,
            description=wrapped_tool.description,
            wrapped_tool=wrapped_tool,
            user_query=user_query,
            agent=agent,
            response_processing_size_threshold=response_processing_size_threshold,
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """执行工具并处理结果

        契约：
        - 输入：位置参数和关键字参数
        - 输出：处理后的结果字符串
        - 副作用：执行被包装的工具和后处理
        - 失败语义：如果后处理失败，返回原始结果
        """
        # 执行已包装的工具
        result = self._execute_tool(*args, **kwargs)

        try:
            # 执行后处理并返回结果
            return self.process_tool_response(result)
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            # 后处理失败则记录错误并返回原结果
            logger.error(f"Error in post-processing tool response: {e}")
            return result

    def _get_tool_response_str(self, tool_response) -> str:
        """将各种工具响应格式转换为字符串表示

        契约：
        - 输入：工具响应（多种可能的类型）
        - 输出：字符串表示的工具响应
        - 副作用：无
        - 失败语义：如果响应为 None，返回空字符串
        """
        if isinstance(tool_response, str):
            tool_response_str = tool_response
        elif isinstance(tool_response, Data):
            tool_response_str = str(tool_response.data)
        elif isinstance(tool_response, list) and all(isinstance(item, Data) for item in tool_response):
            # 仅取首元素（是否应取首或末仍待确认）
            tool_response_str = str(tool_response[0].data)
        elif isinstance(tool_response, (dict, list)):
            tool_response_str = str(tool_response)
        else:
            # 返回空字符串而非 `None` 以避免类型错误
            tool_response_str = str(tool_response) if tool_response is not None else ""

        return tool_response_str

    def process_tool_response(self, tool_response: str, **_kwargs) -> str:
        """处理工具响应

        关键路径（三步）：
        1) 检查响应是否为错误消息
        2) 尝试将响应解析为 JSON
        3) 如果是大 JSON，则使用代码生成组件处理

        异常流：JSON 解析失败时跳过代码生成。
        性能瓶颈：代码生成组件执行时。
        排障入口：日志关键字 "An error in converting the tool response to json"、"Output of CodeGenerationComponent"。
        
        契约：
        - 输入：工具响应字符串和其他参数
        - 输出：处理后的响应字符串
        - 副作用：可能调用代码生成组件
        - 失败语义：如果处理失败，返回原始工具响应
        """
        logger.info("Calling process_tool_response of PostToolProcessor")
        tool_response_str = self._get_tool_response_str(tool_response)

        # 先判断是否为带项目符号的错误消息（`SPARC` 拒绝）
        if "❌" in tool_response_str or "•" in tool_response_str:
            logger.info("Detected error message with special characters, skipping JSON parsing")
            return tool_response_str

        try:
        # 仅对疑似 `JSON` 的内容尝试解析
            if (tool_response_str.startswith("{") and tool_response_str.endswith("}")) or (
                tool_response_str.startswith("[") and tool_response_str.endswith("]")
            ):
                tool_response_json = ast.literal_eval(tool_response_str)
                if not isinstance(tool_response_json, (list, dict)):
                    tool_response_json = None
            else:
                tool_response_json = None
        except (json.JSONDecodeError, TypeError, SyntaxError, ValueError) as e:
            logger.info(
                f"An error in converting the tool response to json, this will skip the code generation component: {e}"
            )
            tool_response_json = None

        if tool_response_json is not None and len(str(tool_response_json)) > self.response_processing_size_threshold:
            llm_client_obj = self._get_altk_llm_object(use_output_val=False)
            if llm_client_obj is not None:
                config = CodeGenerationComponentConfig(llm_client=llm_client_obj, use_docker_sandbox=False)

                middleware = CodeGenerationComponent(config=config)
                input_data = CodeGenerationRunInput(
                    messages=[],
                    nl_query=self.user_query,
                    tool_response=tool_response_json,
                )
                output = None
                try:
                    output = middleware.process(input_data, AgentPhase.RUNTIME)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Exception in executing CodeGenerationComponent: {e}")
                if output is not None and hasattr(output, "result"):
                    logger.info(f"Output of CodeGenerationComponent: {output.result}")
                    return output.result
        return tool_response


class PostToolProcessingWrapper(BaseToolWrapper):
    """添加后工具处理功能的工具包装器

    此包装器处理工具调用的输出，特别是 JSON 响应，
    使用 ALTK 代码生成组件提取有用信息。
    
    契约：
    - 输入：BaseTool 对象及额外参数
    - 输出：带有后处理功能的包装工具
    - 副作用：初始化后处理组件
    - 失败语义：如果包装失败，返回原始工具
    """

    def __init__(self, response_processing_size_threshold: int = 100):
        """初始化后工具处理包装器

        契约：
        - 输入：响应处理大小阈值
        - 输出：PostToolProcessingWrapper 实例
        - 副作用：初始化阈值属性
        - 失败语义：无
        """
        self.response_processing_size_threshold = response_processing_size_threshold

    def wrap_tool(self, tool: BaseTool, **kwargs) -> BaseTool:
        """使用后处理功能包装工具

        关键路径（三步）：
        1) 检查工具是否已被相同包装器包装
        2) 验证必要参数是否存在
        3) 应用后处理包装器

        异常流：缺少代理参数时返回原始工具。
        性能瓶颈：无显著性能瓶颈。
        排障入口：日志关键字 "Cannot wrap tool with PostToolProcessor"。
        
        契约：
        - 输入：BaseTool 对象和关键字参数
        - 输出：包装后的 BaseTool 对象
        - 副作用：初始化后处理组件
        - 失败语义：如果必要参数缺失，返回原始工具
        """
        logger.info(f"Post-tool reflection enabled for {tool.name}")
        if isinstance(tool, PostToolProcessor):
            # 已被该包装器包裹则直接返回
            return tool

        # 必需的 `kwargs`
        agent = kwargs.get("agent")
        user_query = kwargs.get("user_query", "")

        if not agent:
            logger.warning("Cannot wrap tool with PostToolProcessor: missing 'agent'")
            return tool

        # 若工具已被其他包装器包裹，则需获取最内层工具
        actual_tool = tool

        return PostToolProcessor(
            wrapped_tool=actual_tool,
            user_query=user_query,
            agent=agent,
            response_processing_size_threshold=self.response_processing_size_threshold,
        )
