"""
模块名称：变量管理接口

本模块提供用户变量的增删改查，并在模型提供方凭据变更时清理相关模型状态。
主要功能：
- 创建/更新/删除用户变量
- 校验模型提供方 API Key
- 删除凭据时清理启用/禁用模型列表
设计背景：统一变量存储与模型凭据管理入口。
注意事项：模型凭据校验在后台线程执行，避免阻塞事件循环。
"""

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from lfx.base.models.unified_models import get_model_provider_variable_mapping, validate_model_provider_key
from sqlalchemy.exc import NoResultFound

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.v1.models import (
    DISABLED_MODELS_VAR,
    ENABLED_MODELS_VAR,
    get_model_names_for_provider,
    get_provider_from_variable_name,
)
from langflow.services.database.models.variable.model import VariableCreate, VariableRead, VariableUpdate
from langflow.services.deps import get_variable_service
from langflow.services.variable.constants import CREDENTIAL_TYPE, GENERIC_TYPE
from langflow.services.variable.service import DatabaseVariableService

router = APIRouter(prefix="/variables", tags=["Variables"])
model_provider_variable_mapping = get_model_provider_variable_mapping()
logger = logging.getLogger(__name__)


async def _cleanup_model_list_variable(
    variable_service: DatabaseVariableService,
    user_id: UUID,
    variable_name: str,
    models_to_remove: set[str],
    session: DbSession,
) -> None:
    """从模型列表变量中移除指定模型。

    契约：
    - 输入：变量名与待移除模型集合
    - 输出：无
    - 副作用：更新或删除变量记录

    关键路径（三步）：
    1) 读取变量并解析当前模型集合
    2) 计算差集并判断是否需要更新
    3) 根据结果更新或删除变量
    """
    try:
        model_list_var = await variable_service.get_variable_object(
            user_id=user_id, name=variable_name, session=session
        )
    except ValueError:
        # 注意：变量不存在直接返回。
        return

    if not model_list_var or not model_list_var.value:
        return

    # 实现：解析当前模型集合。
    try:
        current_models = set(json.loads(model_list_var.value))
    except (json.JSONDecodeError, TypeError):
        current_models = set()

    # 实现：移除待清理模型。
    filtered_models = current_models - models_to_remove

    # 注意：无变化则跳过写入。
    if filtered_models == current_models:
        return

    if filtered_models:
        # 实现：更新过滤后的列表。
        if model_list_var.id is not None:
            await variable_service.update_variable_fields(
                user_id=user_id,
                variable_id=model_list_var.id,
                variable=VariableUpdate(
                    id=model_list_var.id,
                    name=variable_name,
                    value=json.dumps(list(filtered_models)),
                    type=GENERIC_TYPE,
                ),
                session=session,
            )
    else:
        # 实现：列表为空时删除变量。
        await variable_service.delete_variable(user_id=user_id, name=variable_name, session=session)


async def _cleanup_provider_models(
    variable_service: DatabaseVariableService,
    user_id: UUID,
    provider: str,
    session: DbSession,
) -> None:
    """清理已删除提供方凭据关联的模型状态列表。"""
    try:
        provider_models = get_model_names_for_provider(provider)
    except ValueError:
        logger.exception("Provider model retrieval failed")
        return

    # 实现：同步清理启用/禁用模型列表。
    await _cleanup_model_list_variable(variable_service, user_id, DISABLED_MODELS_VAR, provider_models, session)
    await _cleanup_model_list_variable(variable_service, user_id, ENABLED_MODELS_VAR, provider_models, session)


@router.post("/", response_model=VariableRead, status_code=201)
async def create_variable(
    *,
    session: DbSession,
    variable: VariableCreate,
    current_user: CurrentActiveUser,
):
    """创建新变量。"""
    variable_service = get_variable_service()
    if not variable.name and not variable.value:
        raise HTTPException(status_code=400, detail="Variable name and value cannot be empty")

    if not variable.name:
        raise HTTPException(status_code=400, detail="Variable name cannot be empty")

    if not variable.value:
        raise HTTPException(status_code=400, detail="Variable value cannot be empty")

    if variable.name in await variable_service.list_variables(user_id=current_user.id, session=session):
        raise HTTPException(status_code=400, detail="Variable name already exists")

    # 注意：若为模型提供方变量，需要校验凭据有效性。
    if variable.name in model_provider_variable_mapping.values():
        # 注意：使用后台线程校验，避免阻塞事件循环。
        try:
            await asyncio.to_thread(validate_model_provider_key, variable.name, variable.value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        return await variable_service.create_variable(
            user_id=current_user.id,
            name=variable.name,
            value=variable.value,
            default_fields=variable.default_fields or [],
            type_=variable.type or CREDENTIAL_TYPE,
            session=session,
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/", response_model=list[VariableRead], status_code=200)
async def read_variables(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """读取当前用户所有变量。"""
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        msg = "Variable service is not an instance of DatabaseVariableService"
        raise TypeError(msg)
    try:
        all_variables = await variable_service.get_all(user_id=current_user.id, session=session)
        # 注意：过滤内部变量（`__xxx__`）。
        return [
            var for var in all_variables if not (var.name and var.name.startswith("__") and var.name.endswith("__"))
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch("/{variable_id}", response_model=VariableRead, status_code=200)
async def update_variable(
    *,
    session: DbSession,
    variable_id: UUID,
    variable: VariableUpdate,
    current_user: CurrentActiveUser,
):
    """更新变量。"""
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        msg = "Variable service is not an instance of DatabaseVariableService"
        raise TypeError(msg)
    try:
        # 实现：读取旧变量以判断是否为模型提供方凭据。
        existing_variable = await variable_service.get_variable_by_id(
            user_id=current_user.id, variable_id=variable_id, session=session
        )

        # 注意：更新模型提供方凭据时需校验有效性。
        if existing_variable.name in model_provider_variable_mapping.values() and variable.value:
            # 注意：使用后台线程校验，避免阻塞事件循环。
            try:
                await asyncio.to_thread(validate_model_provider_key, existing_variable.name, variable.value)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

        return await variable_service.update_variable_fields(
            user_id=current_user.id,
            variable_id=variable_id,
            variable=variable,
            session=session,
        )
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Variable not found") from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Variable not found") from e
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{variable_id}", status_code=204)
async def delete_variable(
    *,
    session: DbSession,
    variable_id: UUID,
    current_user: CurrentActiveUser,
) -> None:
    """删除变量，并在需要时清理模型状态。

    失败语义：删除失败返回 500。
    """
    variable_service = get_variable_service()
    try:
        # 实现：删除前读取变量以判断是否为提供方凭据。
        variable_to_delete = await variable_service.get_variable_by_id(
            user_id=current_user.id, variable_id=variable_id, session=session
        )

        # 实现：获取提供方名称用于清理关联模型。
        provider = get_provider_from_variable_name(variable_to_delete.name)

        # 实现：删除变量记录。
        await variable_service.delete_variable_by_id(user_id=current_user.id, variable_id=variable_id, session=session)

        # 注意：仅在删除提供方凭据时清理模型状态。
        if provider and isinstance(variable_service, DatabaseVariableService):
            await _cleanup_provider_models(variable_service, current_user.id, provider, session)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
