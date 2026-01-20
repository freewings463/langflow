"""
模块名称：代码与提示词校验接口

本模块提供代码片段与提示词模板的校验入口，返回结构化错误或输入变量集合。
主要功能：
- 执行自定义代码静态校验并返回分组错误
- 解析提示词模板并输出变量列表
设计背景：在保存或运行前给出可理解的校验反馈。
注意事项：异常统一转换为 500；前端需展示错误详情。
"""

from fastapi import APIRouter, Depends, HTTPException
from lfx.base.prompts.api_utils import process_prompt_template
from lfx.custom.validate import validate_code
from lfx.log.logger import logger

from langflow.api.v1.base import Code, CodeValidationResponse, PromptValidationResponse, ValidatePromptRequest
from langflow.services.auth.utils import get_current_active_user

# 实现：统一挂载 `/validate` 前缀。
router = APIRouter(prefix="/validate", tags=["Validate"])


@router.post("/code", status_code=200, dependencies=[Depends(get_current_active_user)])
async def post_validate_code(code: Code) -> CodeValidationResponse:
    """校验代码片段的导入与函数结构。

    契约：
    - 输入：`code.code` 为源码文本
    - 输出：`CodeValidationResponse`（`imports`/`function` 错误分组）
    - 失败语义：异常转 `HTTPException(500)`，前端需提示错误
    """
    try:
        errors = validate_code(code.code)
        return CodeValidationResponse(
            imports=errors.get("imports", {}),
            function=errors.get("function", {}),
        )
    except Exception as e:
        logger.debug("Error validating code", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/prompt", status_code=200, dependencies=[Depends(get_current_active_user)])
async def post_validate_prompt(
    prompt_request: ValidatePromptRequest,
) -> PromptValidationResponse:
    """校验提示词模板并输出输入变量列表。

    契约：
    - 输入：`ValidatePromptRequest`
    - 输出：`PromptValidationResponse`
    - 失败语义：异常转 `HTTPException(500)`，调用方需提示错误

    关键路径（三步）：
    1) 校验 `frontend_node` 是否存在
    2) 使用模板解析得到输入变量
    3) 组装响应并返回
    """
    try:
        if not prompt_request.frontend_node:
            return PromptValidationResponse(
                input_variables=[],
                frontend_node=None,
            )

        # 实现：按前端节点参数解析模板变量。
        input_variables = process_prompt_template(
            template=prompt_request.template,
            name=prompt_request.name,
            custom_fields=prompt_request.frontend_node.custom_fields,
            frontend_node_template=prompt_request.frontend_node.template,
            is_mustache=prompt_request.mustache,
        )

        return PromptValidationResponse(
            input_variables=input_variables,
            frontend_node=prompt_request.frontend_node,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
