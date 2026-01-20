"""OpenAI Responses 兼容 schema。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class OpenAIResponsesRequest(BaseModel):
    """OpenAI Responses 请求结构（model 字段用于 flow_id）。"""

    model: str = Field(..., description="The flow ID to execute (used instead of OpenAI model)")
    input: str = Field(..., description="The input text to process")
    stream: bool = Field(default=False, description="Whether to stream the response")
    background: bool = Field(default=False, description="Whether to process in background")
    tools: list[Any] | None = Field(default=None, description="Tools are not supported yet")
    previous_response_id: str | None = Field(
        default=None, description="ID of previous response to continue conversation"
    )
    include: list[str] | None = Field(
        default=None, description="Additional response data to include, e.g., ['tool_call.results']"
    )


class OpenAIResponsesResponse(BaseModel):
    """OpenAI Responses 响应结构。"""

    id: str
    object: Literal["response"] = "response"
    created_at: int
    status: Literal["completed", "in_progress", "failed"] = "completed"
    error: dict | None = None
    incomplete_details: dict | None = None
    instructions: str | None = None
    max_output_tokens: int | None = None
    model: str
    output: list[dict]
    parallel_tool_calls: bool = True
    previous_response_id: str | None = None
    reasoning: dict = Field(default_factory=lambda: {"effort": None, "summary": None})
    store: bool = True
    temperature: float = 1.0
    text: dict = Field(default_factory=lambda: {"format": {"type": "text"}})
    tool_choice: str = "auto"
    tools: list[dict] = Field(default_factory=list)
    top_p: float = 1.0
    truncation: str = "disabled"
    usage: dict | None = None
    user: str | None = None
    metadata: dict = Field(default_factory=dict)


class OpenAIResponsesStreamChunk(BaseModel):
    """OpenAI Responses 流式分片结构。"""

    id: str
    object: Literal["response.chunk"] = "response.chunk"
    created: int
    model: str
    delta: dict
    status: Literal["completed", "in_progress", "failed"] | None = None


class OpenAIErrorResponse(BaseModel):
    error: dict = Field(..., description="Error details")


def create_openai_error(message: str, type_: str = "invalid_request_error", code: str | None = None) -> dict:
    """创建 OpenAI 兼容错误响应。"""
    error_data = {
        "message": message,
        "type": type_,
    }
    if code:
        error_data["code"] = code

    return {"error": error_data}
