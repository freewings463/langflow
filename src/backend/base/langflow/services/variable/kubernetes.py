"""
模块名称：Kubernetes 变量服务实现

本模块实现基于 Kubernetes Secrets 的变量服务，提供变量的安全存储和管理。
主要功能包括：
- 通过 Kubernetes Secrets 存储和管理变量
- 支持凭据类型和通用类型变量
- 提供变量的加密和解密功能

关键组件：
- `KubernetesSecretService`

设计背景：在 Kubernetes 环境中安全存储敏感变量，如 API 密钥等。
注意事项：需要集群访问权限，依赖 Kubernetes API。
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from lfx.log.logger import logger
from typing_extensions import override

from langflow.services.auth import utils as auth_utils
from langflow.services.base import Service
from langflow.services.database.models.variable.model import Variable, VariableCreate, VariableRead, VariableUpdate
from langflow.services.variable.base import VariableService
from langflow.services.variable.constants import CREDENTIAL_TYPE, GENERIC_TYPE
from langflow.services.variable.kubernetes_secrets import KubernetesSecretManager, encode_user_id

if TYPE_CHECKING:
    from uuid import UUID

    from lfx.services.settings.service import SettingsService
    from sqlmodel import Session
    from sqlmodel.ext.asyncio.session import AsyncSession


class KubernetesSecretService(VariableService, Service):
    def __init__(self, settings_service: SettingsService):
        """初始化 Kubernetes 变量服务。

        契约：设置 Kubernetes Secret 管理器并存储配置服务。
        副作用：创建 KubernetesSecretManager 实例。
        失败语义：Kubernetes 配置无效时会抛出异常。
        """
        self.settings_service = settings_service
        # TODO: settings_service to set kubernetes namespace
        self.kubernetes_secrets = KubernetesSecretManager()

    @override
    async def initialize_user_variables(self, user_id: UUID | str, session: AsyncSession) -> None:
        """初始化用户变量。

        契约：从环境变量中读取指定变量并存储到 Kubernetes Secret 中。
        副作用：创建或更新用户的 Kubernetes Secret。
        失败语义：创建 Secret 失败时记录异常但不中断执行。
        """
        # Check for environment variables that should be stored in the database
        should_or_should_not = "Should" if self.settings_service.settings.store_environment_variables else "Should not"
        await logger.ainfo(f"{should_or_should_not} store environment variables in the kubernetes.")
        if self.settings_service.settings.store_environment_variables:
            variables = {}
            for var in self.settings_service.settings.variables_to_get_from_environment:
                if var in os.environ:
                    await logger.adebug(f"Creating {var} variable from environment.")
                    value = os.environ[var]
                    if isinstance(value, str):
                        value = value.strip()
                    key = CREDENTIAL_TYPE + "_" + var
                    variables[key] = str(value)

            try:
                secret_name = encode_user_id(user_id)
                await asyncio.to_thread(
                    self.kubernetes_secrets.create_secret,
                    name=secret_name,
                    data=variables,
                )
            except Exception:  # noqa: BLE001
                logger.exception(f"Error creating {var} variable")

        else:
            logger.info("Skipping environment variable storage.")

    # resolve_variable is a helper function that resolves the variable name to the actual key in the secret
    def resolve_variable(
        self,
        secret_name: str,
        user_id: UUID | str,
        name: str,
    ) -> tuple[str, str]:
        """解析变量名到 Secret 中的实际键名。

        契约：根据变量名查找对应的 Secret 键值对。
        副作用：无。
        失败语义：变量不存在时抛出 ValueError。
        """
        variables = self.kubernetes_secrets.get_secret(name=secret_name)
        if not variables:
            msg = f"user_id {user_id} variable not found."
            raise ValueError(msg)

        if name in variables:
            return name, variables[name]
        credential_name = CREDENTIAL_TYPE + "_" + name
        if credential_name in variables:
            return credential_name, variables[credential_name]
        msg = f"user_id {user_id} variable name {name} not found."
        raise ValueError(msg)

    @override
    async def get_variable(self, user_id: UUID | str, name: str, field: str, session: AsyncSession) -> str:
        """获取变量值。

        契约：根据用户ID和变量名获取变量值。
        副作用：无。
        失败语义：变量不存在或类型不匹配时抛出异常。
        """
        secret_name = encode_user_id(user_id)
        key, value = await asyncio.to_thread(self.resolve_variable, secret_name, user_id, name)
        if key.startswith(CREDENTIAL_TYPE + "_") and field == "session_id":
            msg = (
                f"variable {name} of type 'Credential' cannot be used in a Session ID field "
                "because its purpose is to prevent the exposure of values."
            )
            raise TypeError(msg)
        return value

    @override
    async def list_variables(
        self,
        user_id: UUID | str,
        session: Session,
    ) -> list[str | None]:
        """列出所有变量。

        契约：返回指定用户的所有变量名列表。
        副作用：无。
        失败语义：获取 Secret 失败时返回空列表。
        """
        variables = await asyncio.to_thread(self.kubernetes_secrets.get_secret, name=encode_user_id(user_id))
        if not variables:
            return []

        names = []
        for key in variables:
            if key.startswith(CREDENTIAL_TYPE + "_"):
                names.append(key[len(CREDENTIAL_TYPE) + 1 :])
            else:
                names.append(key)
        return names

    def _update_variable(
        self,
        user_id: UUID | str,
        name: str,
        value: str,
    ):
        """更新变量的内部实现。

        契约：更新指定用户的变量值。
        副作用：修改 Kubernetes Secret 中的值。
        失败语义：变量不存在或更新失败时抛出异常。
        """
        secret_name = encode_user_id(user_id)
        secret_key, _ = self.resolve_variable(secret_name, user_id, name)
        return self.kubernetes_secrets.update_secret(name=secret_name, data={secret_key: value})

    @override
    async def update_variable(
        self,
        user_id: UUID | str,
        name: str,
        value: str,
        session: AsyncSession,
    ):
        """更新变量。

        契约：异步更新指定用户的变量值。
        副作用：修改 Kubernetes Secret 中的值。
        失败语义：变量不存在或更新失败时抛出异常。
        """
        return await asyncio.to_thread(self._update_variable, user_id, name, value)

    def _delete_variable(self, user_id: UUID | str, name: str) -> None:
        """删除变量的内部实现。

        契约：从 Kubernetes Secret 中删除指定变量。
        副作用：修改 Kubernetes Secret 中的键值对。
        失败语义：变量不存在或删除失败时抛出异常。
        """
        secret_name = encode_user_id(user_id)
        secret_key, _ = self.resolve_variable(secret_name, user_id, name)
        self.kubernetes_secrets.delete_secret_key(name=secret_name, key=secret_key)

    @override
    async def delete_variable(self, user_id: UUID | str, name: str, session: AsyncSession) -> None:
        """删除变量。

        契约：异步删除指定用户的变量。
        副作用：从 Kubernetes Secret 中删除变量。
        失败语义：变量不存在或删除失败时抛出异常。
        """
        await asyncio.to_thread(self._delete_variable, user_id, name)

    @override
    async def delete_variable_by_id(self, user_id: UUID | str, variable_id: UUID | str, session: AsyncSession) -> None:
        """通过ID删除变量。

        契约：异步删除指定用户的变量（通过ID）。
        副作用：从 Kubernetes Secret 中删除变量。
        失败语义：变量不存在或删除失败时抛出异常。
        """
        await self.delete_variable(user_id, str(variable_id), session)

    @override
    async def create_variable(
        self,
        user_id: UUID | str,
        name: str,
        value: str,
        *,
        default_fields: list[str],
        type_: str,
        session: AsyncSession,
    ) -> Variable:
        """创建变量。

        契约：异步创建指定用户的变量。
        副作用：在 Kubernetes Secret 中创建新的键值对。
        失败语义：创建失败时抛出异常。
        """
        secret_name = encode_user_id(user_id)
        secret_key = name
        if type_ == CREDENTIAL_TYPE:
            secret_key = CREDENTIAL_TYPE + "_" + name
        else:
            type_ = GENERIC_TYPE

        await asyncio.to_thread(
            self.kubernetes_secrets.upsert_secret, secret_name=secret_name, data={secret_key: value}
        )

        variable_base = VariableCreate(
            name=name,
            type=type_,
            value=auth_utils.encrypt_api_key(value, settings_service=self.settings_service),
            default_fields=default_fields,
        )
        return Variable.model_validate(variable_base, from_attributes=True, update={"user_id": user_id})

    @override
    async def get_all(self, user_id: UUID | str, session: AsyncSession) -> list[VariableRead]:
        """获取所有变量。

        契约：返回指定用户的所有变量。
        副作用：无。
        失败语义：获取 Secret 失败时返回空列表。
        """
        secret_name = encode_user_id(user_id)
        variables = await asyncio.to_thread(self.kubernetes_secrets.get_secret, name=secret_name)
        if not variables:
            return []

        variables_read = []
        for key, value in variables.items():
            name = key
            type_ = GENERIC_TYPE
            if key.startswith(CREDENTIAL_TYPE + "_"):
                name = key[len(CREDENTIAL_TYPE) + 1 :]
                type_ = CREDENTIAL_TYPE

            decrypted_value = None
            if type_ == GENERIC_TYPE:
                decrypted_value = value

            variable_base = VariableCreate(
                name=name,
                type=type_,
                value=auth_utils.encrypt_api_key(value, settings_service=self.settings_service),
                default_fields=[],
            )
            variable = Variable.model_validate(variable_base, from_attributes=True, update={"user_id": user_id})
            variable_read = VariableRead.model_validate(variable, from_attributes=True)
            variable_read.value = decrypted_value
            variables_read.append(variable_read)

        return variables_read

    @override
    async def get_variable_by_id(self, user_id: UUID | str, variable_id: UUID | str, session: AsyncSession) -> Variable:
        """通过ID获取变量。

        契约：根据变量ID获取变量对象。
        副作用：无。
        失败语义：变量不存在时抛出异常。
        
        注意：Kubernetes secrets 没有ID概念，所以使用 variable_id 作为名称。
        """
        secret_name = encode_user_id(user_id)
        key, value = await asyncio.to_thread(self.resolve_variable, secret_name, user_id, str(variable_id))

        name = key
        type_ = GENERIC_TYPE
        if key.startswith(CREDENTIAL_TYPE + "_"):
            name = key[len(CREDENTIAL_TYPE) + 1 :]
            type_ = CREDENTIAL_TYPE

        variable_base = VariableCreate(
            name=name,
            type=type_,
            value=auth_utils.encrypt_api_key(value, settings_service=self.settings_service),
            default_fields=[],
        )
        return Variable.model_validate(variable_base, from_attributes=True, update={"user_id": user_id})

    @override
    async def get_variable_object(self, user_id: UUID | str, name: str, session: AsyncSession) -> Variable:
        """通过名称获取变量对象。

        契约：根据变量名获取变量对象。
        副作用：无。
        失败语义：变量不存在时抛出异常。
        """
        secret_name = encode_user_id(user_id)
        key, value = await asyncio.to_thread(self.resolve_variable, secret_name, user_id, name)

        var_name = key
        type_ = GENERIC_TYPE
        if key.startswith(CREDENTIAL_TYPE + "_"):
            var_name = key[len(CREDENTIAL_TYPE) + 1 :]
            type_ = CREDENTIAL_TYPE

        variable_base = VariableCreate(
            name=var_name,
            type=type_,
            value=auth_utils.encrypt_api_key(value, settings_service=self.settings_service),
            default_fields=[],
        )
        return Variable.model_validate(variable_base, from_attributes=True, update={"user_id": user_id})

    @override
    async def update_variable_fields(
        self, user_id: UUID | str, variable_id: UUID | str, variable: VariableUpdate, session: AsyncSession
    ) -> Variable:
        """更新变量的特定字段。

        契约：更新指定变量的特定字段。
        副作用：修改 Kubernetes Secret 中的值。
        失败语义：变量不存在或更新失败时抛出异常。
        
        注意：Kubernetes secrets 没有ID概念，所以使用变量名称进行更新。
        """
        if variable.name:
            name = variable.name
        else:
            # Try to get the current variable to find its name
            current_var = await self.get_variable_by_id(user_id, variable_id, session)
            name = current_var.name

        if variable.value is not None:
            await self.update_variable(user_id, name, variable.value, session)

        # Return the updated variable
        return await self.get_variable_object(user_id, name, session)
