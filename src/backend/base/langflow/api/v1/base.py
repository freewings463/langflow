"""
模块名称：基础请求与响应模型

本模块提供 API v1 常用的轻量级 Pydantic 模型，覆盖缓存响应、前端节点序列化与提示词校验入参。
主要功能：
- 统一缓存与代码片段返回结构
- 兼容前端节点序列化需求
- 规范提示词校验与校验结果结构
设计背景：为 API 层提供稳定的输入输出契约，避免重复声明模型。
注意事项：`FrontendNodeRequest` 会覆盖序列化以移除前端不需要的包装字段。
"""

from lfx.template.frontend_node.base import FrontendNode
from pydantic import BaseModel, field_validator, model_serializer


class CacheResponse(BaseModel):
    """缓存读取响应。

    契约：
    - 输出：`data` 为缓存命中结果的原始字典
    """

    data: dict


class Code(BaseModel):
    """代码片段载体，用于接口间传递源码字符串。"""

    code: str


class FrontendNodeRequest(FrontendNode):
    """前端节点请求模型，兼容 API 序列化约束。"""

    template: dict  # type: ignore[assignment]

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        """移除 `FrontendNode` 默认的 `name` 包装。"""
        # 实现：API 响应无需 `{name: {...}}` 外层，避免前端二次解包。
        return handler(self)


class ValidatePromptRequest(BaseModel):
    """提示词校验请求。

    契约：`template` 为待校验模板，`custom_fields` 用于扩展字段占位。
    """

    name: str
    template: str
    custom_fields: dict | None = None
    frontend_node: FrontendNodeRequest | None = None
    mustache: bool = False


# 实现：构建兼容校验返回结构 `{"imports": {"errors": []}, "function": {"errors": []}}`。
class CodeValidationResponse(BaseModel):
    """代码校验响应，分别暴露 `imports` 与 `function` 维度错误。"""

    imports: dict
    function: dict

    @field_validator("imports")
    @classmethod
    def validate_imports(cls, v):
        return v or {"errors": []}

    @field_validator("function")
    @classmethod
    def validate_function(cls, v):
        return v or {"errors": []}


class PromptValidationResponse(BaseModel):
    """提示词校验响应，返回输入变量与前端节点结构。"""

    input_variables: list
    # 注意：用于 `tweak` 调用的对象结构返回。
    frontend_node: FrontendNodeRequest | None = None
