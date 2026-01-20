"""
模块名称：模型选项接口

本模块暴露语言模型与向量模型的可选项列表，并按用户启用的供应商与模型过滤。
主要功能：
- 返回语言模型可选项
- 返回向量模型可选项
设计背景：前端需要基于用户权限动态展示模型下拉列表。
注意事项：依赖 `CurrentActiveUser`，未授权用户无法获取列表。
"""

from fastapi import APIRouter
from lfx.base.models.unified_models import get_embedding_model_options, get_language_model_options

from langflow.api.utils import CurrentActiveUser

router = APIRouter(prefix="/model_options", tags=["Model Options"])


@router.get("/language", status_code=200)
async def get_language_model_options_endpoint(
    current_user: CurrentActiveUser,
):
    """获取当前用户可用的语言模型列表。

    契约：
    - 输入：`current_user`
    - 输出：模型选项列表（按用户启用的提供方过滤）
    """
    return get_language_model_options(user_id=current_user.id)


@router.get("/embedding", status_code=200)
async def get_embedding_model_options_endpoint(
    current_user: CurrentActiveUser,
):
    """获取当前用户可用的向量模型列表。

    契约：
    - 输入：`current_user`
    - 输出：模型选项列表（按用户启用的提供方过滤）
    """
    return get_embedding_model_options(user_id=current_user.id)
