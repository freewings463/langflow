"""
模块名称：变量服务抽象基类

本模块定义变量服务的抽象接口，规范变量管理操作。
主要功能包括：
- 定义变量服务的标准操作接口
- 提供变量的增删改查抽象方法

关键组件：
- `VariableService`

设计背景：统一不同存储后端（数据库、Kubernetes等）的变量管理接口。
注意事项：实现类必须遵循方法签名与行为约定。
"""

import abc
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.base import Service
from langflow.services.database.models.variable.model import Variable, VariableRead, VariableUpdate


class VariableService(Service):
    """变量服务抽象基类。

    契约：定义变量服务的标准接口，包含变量的增删改查等操作。
    失败语义：所有方法均为抽象方法，必须由子类实现。
    """

    name = "variable_service"

    @abc.abstractmethod
    async def initialize_user_variables(self, user_id: UUID | str, session: AsyncSession) -> None:
        """初始化用户变量。

        契约：为指定用户初始化环境变量或默认变量。
        副作用：可能创建新的变量记录。
        失败语义：会抛出实现特定的异常。
        """

    @abc.abstractmethod
    async def get_variable(self, user_id: UUID | str, name: str, field: str, session: AsyncSession) -> str:
        """异步获取变量值。

        契约：根据用户ID、变量名和字段获取变量值。
        副作用：无。
        失败语义：变量不存在时会抛出异常。
        """

    @abc.abstractmethod
    async def list_variables(self, user_id: UUID | str, session: AsyncSession) -> list[str | None]:
        """列出所有变量。

        契约：返回指定用户的所有变量名列表。
        副作用：无。
        失败语义：会抛出实现特定的异常。
        """

    @abc.abstractmethod
    async def update_variable(self, user_id: UUID | str, name: str, value: str, session: AsyncSession) -> Variable:
        """更新变量。

        契约：更新指定用户的变量值。
        副作用：修改数据库中的变量记录。
        失败语义：变量不存在或更新失败时会抛出异常。
        """

    @abc.abstractmethod
    async def delete_variable(self, user_id: UUID | str, name: str, session: AsyncSession) -> None:
        """删除变量。

        契约：删除指定用户的变量。
        副作用：从数据库中删除变量记录。
        失败语义：变量不存在时会抛出异常。
        """

    @abc.abstractmethod
    async def delete_variable_by_id(self, user_id: UUID | str, variable_id: UUID, session: AsyncSession) -> None:
        """通过ID删除变量。

        契约：根据变量ID删除指定用户的变量。
        副作用：从数据库中删除变量记录。
        失败语义：变量不存在时会抛出异常。
        """

    @abc.abstractmethod
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

        契约：为指定用户创建新变量。
        副作用：在数据库中插入新的变量记录。
        失败语义：创建失败时会抛出异常。
        """

    @abc.abstractmethod
    async def get_all(self, user_id: UUID | str, session: AsyncSession) -> list[VariableRead]:
        """获取所有变量。

        契约：返回指定用户的所有变量。
        副作用：无。
        失败语义：会抛出实现特定的异常。
        """

    @abc.abstractmethod
    async def get_variable_by_id(self, user_id: UUID | str, variable_id: UUID | str, session: AsyncSession) -> Variable:
        """通过ID获取变量。

        契约：根据变量ID获取指定用户的变量。
        副作用：无。
        失败语义：变量不存在时会抛出异常。
        """

    @abc.abstractmethod
    async def get_variable_object(self, user_id: UUID | str, name: str, session: AsyncSession) -> Variable:
        """通过名称获取变量对象。

        契约：根据变量名获取指定用户的变量对象。
        副作用：无。
        失败语义：变量不存在时会抛出异常。
        """

    @abc.abstractmethod
    async def update_variable_fields(
        self, user_id: UUID | str, variable_id: UUID | str, variable: VariableUpdate, session: AsyncSession
    ) -> Variable:
        """更新变量的特定字段。

        契约：更新指定变量的特定字段。
        副作用：修改数据库中的变量记录。
        失败语义：变量不存在或更新失败时会抛出异常。
        """
