"""
模块名称：Telemetry 数据结构

本模块定义 Telemetry 上报所需的 Pydantic 模型与字段别名。
主要功能：
- 统一上报 payload 结构
- 控制 URL 长度与分片策略
- 提供组件输入拆分逻辑
设计背景：保证 Telemetry 数据格式稳定且可追踪。
注意事项：超过 URL 长度限制会触发分片或截断。
"""

from typing import Any

from pydantic import BaseModel, EmailStr, Field

# 注意：Telemetry GET 请求 URL 长度上限（Scarf 像素追踪）。
# Scarf 查询参数最大支持 2KB（2048 字节）。
MAX_TELEMETRY_URL_SIZE = 2048


class BasePayload(BaseModel):
    """Telemetry 基础字段载体。"""

    client_type: str | None = Field(default=None, serialization_alias="clientType")


class RunPayload(BasePayload):
    """流程运行上报载体。"""

    run_is_webhook: bool = Field(default=False, serialization_alias="runIsWebhook")
    run_seconds: int = Field(serialization_alias="runSeconds")
    run_success: bool = Field(serialization_alias="runSuccess")
    run_error_message: str = Field("", serialization_alias="runErrorMessage")
    run_id: str | None = Field(None, serialization_alias="runId")


class ShutdownPayload(BasePayload):
    """进程关闭上报载体。"""

    time_running: int = Field(serialization_alias="timeRunning")


class EmailPayload(BasePayload):
    """注册邮箱上报载体。"""

    email: EmailStr


class VersionPayload(BasePayload):
    """版本与环境信息上报载体。"""

    package: str
    version: str
    platform: str
    python: str
    arch: str
    auto_login: bool = Field(serialization_alias="autoLogin")
    cache_type: str = Field(serialization_alias="cacheType")
    backend_only: bool = Field(serialization_alias="backendOnly")


class PlaygroundPayload(BasePayload):
    """Playground 运行上报载体。"""

    playground_seconds: int = Field(serialization_alias="playgroundSeconds")
    playground_component_count: int | None = Field(None, serialization_alias="playgroundComponentCount")
    playground_success: bool = Field(serialization_alias="playgroundSuccess")
    playground_error_message: str = Field("", serialization_alias="playgroundErrorMessage")
    playground_run_id: str | None = Field(None, serialization_alias="playgroundRunId")


class ComponentPayload(BasePayload):
    """组件执行上报载体。"""

    component_name: str = Field(serialization_alias="componentName")
    component_id: str = Field(serialization_alias="componentId")
    component_seconds: int = Field(serialization_alias="componentSeconds")
    component_success: bool = Field(serialization_alias="componentSuccess")
    component_error_message: str | None = Field(None, serialization_alias="componentErrorMessage")
    component_run_id: str | None = Field(None, serialization_alias="componentRunId")


class ComponentInputsPayload(BasePayload):
    """组件输入上报载体（支持分片）。

    契约：
    - 输入：`component_inputs` 字典
    - 输出：可分片的 payload 列表
    - 失败语义：输入非字典时仅返回自身

    关键路径（三步）：
    1) 计算当前 URL 长度
    2) 逐字段分片，必要时截断超大字段
    3) 生成带 `chunk_index/total_chunks` 的 payload 列表
    """

    component_run_id: str = Field(serialization_alias="componentRunId")
    component_id: str = Field(serialization_alias="componentId")
    component_name: str = Field(serialization_alias="componentName")
    component_inputs: dict[str, Any] = Field(serialization_alias="componentInputs")
    chunk_index: int | None = Field(None, serialization_alias="chunkIndex")
    total_chunks: int | None = Field(None, serialization_alias="totalChunks")

    def _calculate_url_size(self, base_url: str = "https://api.scarf.sh/v1/pixel") -> int:
        """计算编码后的 URL 长度。"""
        from urllib.parse import urlencode

        import orjson

        payload_dict = self.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)
        # Serialize component_inputs dict to JSON string for URL parameter
        if "componentInputs" in payload_dict:
            payload_dict["componentInputs"] = orjson.dumps(payload_dict["componentInputs"]).decode("utf-8")
        # Construct the URL in-memory instead of creating a full HTTPX Request for speed
        query_string = urlencode(payload_dict)
        url = f"{base_url}?{query_string}" if query_string else base_url
        return len(url)

    def _truncate_value_to_fit(self, key: str, value: Any, max_url_size: int) -> Any:
        """二分截断字段值以满足 URL 长度限制。"""
        truncation_suffix = "...[truncated]"

        # 实现：非字符串值先转为字符串再截断。
        str_value = value if isinstance(value, str) else str(value)

        # 实现：二分搜索找到满足长度限制的最大前缀。
        max_len = len(str_value)
        min_len = 0
        truncated_value = str_value[:100] + truncation_suffix  # Initial guess

        while min_len < max_len:
            mid_len = (min_len + max_len + 1) // 2
            test_val = str_value[:mid_len] + truncation_suffix
            test_inputs = {key: test_val}
            test_payload = ComponentInputsPayload(
                component_run_id=self.component_run_id,
                component_id=self.component_id,
                component_name=self.component_name,
                component_inputs=test_inputs,
                chunk_index=0,
                total_chunks=1,
            )

            if test_payload._calculate_url_size() <= max_url_size:
                truncated_value = test_val
                min_len = mid_len
            else:
                max_len = mid_len - 1

        return truncated_value

    def split_if_needed(self, max_url_size: int = MAX_TELEMETRY_URL_SIZE) -> list["ComponentInputsPayload"]:
        """超出长度限制时拆分 payload。"""
        from lfx.log.logger import logger

        # 实现：计算当前 URL 长度。
        current_size = self._calculate_url_size()

        # 注意：未超限直接返回。
        if current_size <= max_url_size:
            return [self]

        # 注意：仅对字典类型做分片。
        if not isinstance(self.component_inputs, dict):
            # 注意：非字典直接返回，避免异常。
            logger.warning(f"component_inputs is not a dict, cannot split: {type(self.component_inputs)}")
            return [self]

        if not self.component_inputs:
            # 注意：空输入直接返回。
            return [self]

        # 实现：按字段分配到多个分片。
        chunks_data = []
        current_chunk_inputs: dict[str, Any] = {}

        for key, value in self.component_inputs.items():
            # 实现：试算加入当前分片后的长度。
            test_inputs = {**current_chunk_inputs, key: value}
            test_payload = ComponentInputsPayload(
                component_run_id=self.component_run_id,
                component_id=self.component_id,
                component_name=self.component_name,
                component_inputs=test_inputs,
                chunk_index=0,
                total_chunks=1,
            )
            test_size = test_payload._calculate_url_size()

            # 实现：超限则开启新分片。
            if test_size > max_url_size and current_chunk_inputs:
                chunks_data.append(current_chunk_inputs)
                # 注意：检查单字段是否仍超限。
                single_field_test = ComponentInputsPayload(
                    component_run_id=self.component_run_id,
                    component_id=self.component_id,
                    component_name=self.component_name,
                    component_inputs={key: value},
                    chunk_index=0,
                    total_chunks=1,
                )
                if single_field_test._calculate_url_size() > max_url_size:
                    # 注意：单字段超限则截断。
                    logger.warning(f"Truncating oversized field '{key}' in component_inputs")
                    truncated_value = self._truncate_value_to_fit(key, value, max_url_size)
                    current_chunk_inputs = {key: truncated_value}
                else:
                    current_chunk_inputs = {key: value}
            elif test_size > max_url_size and not current_chunk_inputs:
                # 注意：单字段超限则截断。
                logger.warning(f"Truncating oversized field '{key}' in component_inputs")

                # 实现：二分截断至可用长度。
                truncated_value = self._truncate_value_to_fit(key, value, max_url_size)
                current_chunk_inputs[key] = truncated_value
            else:
                current_chunk_inputs[key] = value

        # 实现：追加最后一个分片。
        if current_chunk_inputs:
            chunks_data.append(current_chunk_inputs)

        # 实现：构建带序号的分片 payload。
        total_chunks = len(chunks_data)
        result = []

        for chunk_index, chunk_inputs in enumerate(chunks_data):
            chunk_payload = ComponentInputsPayload(
                component_run_id=self.component_run_id,
                component_id=self.component_id,
                component_name=self.component_name,
                component_inputs=chunk_inputs,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
            )
            result.append(chunk_payload)

        return result


class ExceptionPayload(BasePayload):
    """异常上报载体。"""

    exception_type: str = Field(serialization_alias="exceptionType")
    exception_message: str = Field(serialization_alias="exceptionMessage")
    exception_context: str = Field(serialization_alias="exceptionContext")  # "lifespan" or "handler"
    stack_trace_hash: str | None = Field(None, serialization_alias="stackTraceHash")  # Hash for grouping


class ComponentIndexPayload(BasePayload):
    """组件索引上报载体。"""

    index_source: str = Field(serialization_alias="indexSource")  # 注意：取值 "builtin"/"cache"/"dynamic"。
    num_modules: int = Field(serialization_alias="numModules")
    num_components: int = Field(serialization_alias="numComponents")
    dev_mode: bool = Field(serialization_alias="devMode")
    filtered_modules: str | None = Field(None, serialization_alias="filteredModules")  # CSV if filtering
    load_time_ms: int = Field(serialization_alias="loadTimeMs")
