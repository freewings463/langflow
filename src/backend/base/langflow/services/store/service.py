"""
模块名称：`Store` 服务集成实现

本模块实现与 `Store`（`Directus` 实例）交互的服务层，主要用于组件的查询、下载、上传与点赞。
主要功能包括：
- 组件查询过滤、计数与列表响应组装。
- 组件下载/上传/更新与关联 `webhook` 调用。
- 用户上下文缓存、点赞与收藏相关计算。

关键组件：`StoreService`、`user_data_context`、`user_data_var`。
设计背景：统一 `Store` 访问逻辑以降低 `API` 层复杂度。
使用场景：组件市场相关 `API`、组件下载与点赞操作。
注意事项：外部依赖 `Store` 可用性，`HTTP` 异常需在上层转换。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from httpx import HTTPError, HTTPStatusError
from lfx.log.logger import logger

from langflow.services.base import Service
from langflow.services.store.exceptions import APIKeyError, FilterError, ForbiddenError
from langflow.services.store.schema import (
    CreateComponentResponse,
    DownloadComponentResponse,
    ListComponentResponse,
    ListComponentResponseModel,
    StoreComponentCreate,
)
from langflow.services.store.utils import (
    process_component_data,
    process_tags_for_post,
    update_components_with_user_data,
)

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService

from contextlib import asynccontextmanager
from contextvars import ContextVar

user_data_var: ContextVar[dict[str, Any] | None] = ContextVar("user_data", default=None)


@asynccontextmanager
async def user_data_context(store_service: StoreService, api_key: str | None = None):
    """在上下文中加载并缓存用户数据。

    契约：输入 `store_service` 与可选 `api_key`，输出异步上下文管理器。
    副作用：访问远程 `/users/me` 并写入 `user_data_var`。
    关键路径（三步）：
    1) 有 `api_key` 时获取用户信息并写入上下文变量。
    2) 执行上下文内逻辑。
    3) 最终清理上下文变量。
    异常流：鉴权为 403 时抛 `ValueError`。
    排障入口：错误信息 `Invalid API key`。
    决策：使用 `ContextVar` 缓存用户数据
    问题：避免同一请求内重复获取用户信息
    方案：在上下文生命周期内共享用户数据
    代价：必须确保退出时清理变量
    重评：若引入请求级缓存层
    """
    # 注意：进入上下文前加载用户数据以减少重复请求。
    if api_key:
        try:
            user_data, _ = await store_service.get(
                f"{store_service.base_url}/users/me", api_key, params={"fields": "id"}
            )
            user_data_var.set(user_data[0])
        except HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.FORBIDDEN:
                msg = "Invalid API key"
                raise ValueError(msg) from exc
    try:
        yield
    finally:
        # 注意：退出上下文时清理 `ContextVar`，避免泄漏到其他请求。
        user_data_var.set(None)


def get_id_from_search_string(search_string: str) -> str | None:
    """从搜索字符串中提取组件 `UUID`。

    契约：输入 `search_string`，输出 `UUID` 字符串或 `None`；副作用：无。
    关键路径：优先解析 `Store` 链接末尾片段，再尝试 `UUID` 校验。
    决策：兼容直接 `UUID` 与完整链接两种输入
    问题：用户可能粘贴完整 `Store` `URL`
    方案：先做 `URL` 截取，再做 `UUID` 解析
    代价：对非 `UUID` 字符串会多一次解析尝试
    重评：当输入规范收敛为单一格式
    """
    possible_id: str | None = search_string
    if "www.langflow.store/store/" in search_string:
        possible_id = search_string.split("/")[-1]

    try:
        possible_id = str(UUID(search_string))
    except ValueError:
        possible_id = None
    return possible_id


class StoreService(Service):
    """`Store` 服务封装。

    契约：提供查询/下载/上传/点赞等方法，输出 `Pydantic` 响应模型或原始数据。
    关键路径：统一构建过滤条件、调用远程 `Store` 接口并处理错误。
    决策：在服务层集中处理 `Store` 交互
    问题：避免 `API` 层重复组装请求与错误处理
    方案：封装 `GET/POST/PATCH` 与过滤逻辑
    代价：服务层与 `Store` 协议耦合
    重评：若未来更换 `Store` 实现或网关层
    """

    name = "store_service"

    def __init__(self, settings_service: SettingsService):
        """初始化 `Store` 服务配置。

        契约：输入 `settings_service`，输出实例初始化完成；副作用：无。
        关键路径：读取 `store_url` 与相关 `webhook` 配置并组装默认字段。
        决策：在构造期缓存常用 `URL` 与字段
        问题：避免每次请求重复拼接与配置读取
        方案：在 `__init__` 统一初始化
        代价：配置变更需重建服务实例
        重评：若支持运行时动态刷新配置
        """
        self.settings_service = settings_service
        self.base_url = self.settings_service.settings.store_url
        self.download_webhook_url = self.settings_service.settings.download_webhook_url
        self.like_webhook_url = self.settings_service.settings.like_webhook_url
        self.components_url = f"{self.base_url}/items/components"
        self.default_fields = [
            "id",
            "name",
            "description",
            "user_created.username",
            "is_component",
            "tags.tags_id.name",
            "tags.tags_id.id",
            "count(liked_by)",
            "count(downloads)",
            "metadata",
            "last_tested_version",
            "private",
        ]
        self.timeout = 30

    # 注意：使用上下文管理器缓存用户数据，避免重复请求。

    async def check_api_key(self, api_key: str):
        """校验 `API key` 是否有效。

        契约：输入 `api_key`，输出布尔值；副作用：访问远程 `/users/me`。
        关键路径：调用 `get` 并判断返回数据是否包含 `id`。
        异常流：非 401/403 的 `HTTPStatusError` 将转为 `ValueError`。
        决策：将鉴权失败归一为 `False`
        问题：调用方需简单判断 `API key` 是否可用
        方案：401/403 返回 `False`，其他错误向上抛
        代价：无法区分具体鉴权失败原因
        重评：若需要更细粒度的错误反馈
        """
        try:
            user_data, _ = await self.get(f"{self.base_url}/users/me", api_key, params={"fields": "id"})

            return "id" in user_data[0]
        except HTTPStatusError as exc:
            if exc.response.status_code in {403, 401}:
                return False
            msg = f"Unexpected status code: {exc.response.status_code}"
            raise ValueError(msg) from exc
        except Exception as exc:
            msg = f"Unexpected error: {exc}"
            raise ValueError(msg) from exc

    async def get(
        self, url: str, api_key: str | None = None, params: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """执行 `GET` 请求并返回数据与元信息。

        契约：输入 `url/api_key/params`，输出 `(data, meta)`；副作用：发起网络请求。
        关键路径：构造 `Authorization` 头并解析 `data/meta`。
        异常流：`HTTPError` 透传，其它异常转为 `ValueError`。
        决策：统一在此处做响应拆解
        问题：重复解析 `data/meta` 容易出错
        方案：集中解析并返回标准结构
        代价：服务层假设 `Store` 返回结构固定
        重评：若上游响应结构变化
        """
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers, params=params, timeout=self.timeout)
                response.raise_for_status()
            except HTTPError:
                raise
            except Exception as exc:
                msg = f"GET failed: {exc}"
                raise ValueError(msg) from exc
        json_response = response.json()
        result = json_response["data"]
        metadata = {}
        if "meta" in json_response:
            metadata = json_response["meta"]

        if isinstance(result, dict):
            return [result], metadata
        return result, metadata

    async def call_webhook(self, api_key: str, webhook_url: str, component_id: UUID) -> None:
        """调用 `webhook` 通知 `Store` 相关事件。

        契约：输入 `api_key/webhook_url/component_id`，输出 `None`；副作用：发起网络请求。
        关键路径：构造 `Bearer` 头并发送 `POST` 请求。
        异常流：`HTTPError` 透传，其它异常记录日志后吞掉。
        决策：`webhook` 失败不阻断主流程
        问题：下载/点赞流程不应因回调失败而中断
        方案：捕获异常并记录 debug 日志
        代价：`webhook` 失败可能不易被立即发现
        重评：若需要强一致的回调链路
        """
        # 注意：`webhook` 使用 `POST` 且负载包含 `component_id`。
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url, headers=headers, json={"component_id": str(component_id)}, timeout=self.timeout
                )
                response.raise_for_status()
            return response.json()
        except HTTPError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("Webhook failed", exc_info=True)

    @staticmethod
    def build_tags_filter(tags: list[str]):
        """构建标签过滤条件。

        契约：输入标签列表，输出 `Store` 过滤字典；副作用：无。
        关键路径：将每个标签包装为 `_some` 条件并追加 `_and`。
        决策：标签过滤使用 `_and` 组合
        问题：需要同时匹配多个标签
        方案：构造 `_and` 列表
        代价：标签数量越多过滤越严格
        重评：若产品改为 `OR` 匹配
        """
        tags_filter: dict[str, Any] = {"tags": {"_and": []}}
        for tag in tags:
            tags_filter["tags"]["_and"].append({"_some": {"tags_id": {"name": {"_eq": tag}}}})
        return tags_filter

    async def count_components(
        self,
        filter_conditions: list[dict[str, Any]],
        *,
        api_key: str | None = None,
        use_api_key: bool | None = False,
    ) -> int:
        """统计组件数量。

        契约：输入过滤条件与鉴权标志，输出计数；副作用：发起网络请求。
        关键路径：使用聚合接口 `count` 并按 `filter` 过滤。
        决策：仅在需要时附带 `API key`
        问题：避免无效 `API key` 导致 401
        方案：`use_api_key` 控制是否传递
        代价：部分私有计数需显式开启
        重评：若鉴权策略统一由服务层处理
        """
        params = {"aggregate": json.dumps({"count": "*"})}
        if filter_conditions:
            params["filter"] = json.dumps({"_and": filter_conditions})

        api_key = api_key if use_api_key else None

        results, _ = await self.get(self.components_url, api_key, params)
        return int(results[0].get("count", 0))

    @staticmethod
    def build_search_filter_conditions(query: str):
        """构建搜索过滤条件。

        契约：输入查询字符串，输出 `Store` 过滤字典；副作用：无。
        关键路径：在 `name/description/tags/user_created` 上使用 `_icontains`。
        决策：使用 `_icontains` 替代 `search` 参数
        问题：需要大小写不敏感的跨字段搜索
        方案：构造 `_or` 过滤条件
        代价：过滤条件更复杂，查询成本更高
        重评：若 `Store` 支持统一搜索端点
        """
        # 注意：使用 `_icontains` 实现大小写不敏感搜索。
        conditions: dict[str, Any] = {"_or": []}
        conditions["_or"].append({"name": {"_icontains": query}})
        conditions["_or"].append({"description": {"_icontains": query}})
        conditions["_or"].append({"tags": {"tags_id": {"name": {"_icontains": query}}}})
        conditions["_or"].append({"user_created": {"username": {"_icontains": query}}})
        return conditions

    def build_filter_conditions(
        self,
        *,
        component_id: str | None = None,
        search: str | None = None,
        private: bool | None = None,
        tags: list[str] | None = None,
        is_component: bool | None = None,
        filter_by_user: bool | None = False,
        liked: bool | None = False,
        store_api_key: str | None = None,
    ):
        """构建组件过滤条件集合。

        契约：输入筛选参数，输出过滤条件列表；副作用：读取 `user_data_var`。
        关键路径（三步）：
        1) 解析 `component_id/search/private/tags/is_component`。
        2) 处理 `liked/filter_by_user` 与鉴权依赖。
        3) 兜底添加 `private=False` 条件。
        异常流：缺少 `API key` 时抛 `APIKeyError`。
        排障入口：错误信息 `You must provide an API key`。
        决策：默认只返回公开组件
        问题：避免未鉴权时泄露私有组件
        方案：无鉴权时追加 `private=False`
        代价：部分用户期望默认包含私有组件
        重评：若权限策略改为显式过滤
        """
        filter_conditions = []

        if component_id is None:
            component_id = get_id_from_search_string(search) if search else None

        if search is not None and component_id is None:
            search_conditions = self.build_search_filter_conditions(search)
            filter_conditions.append(search_conditions)

        if private is not None:
            filter_conditions.append({"private": {"_eq": private}})

        if tags:
            tags_filter = self.build_tags_filter(tags)
            filter_conditions.append(tags_filter)
        if component_id is not None:
            filter_conditions.append({"id": {"_eq": component_id}})
        if is_component is not None:
            filter_conditions.append({"is_component": {"_eq": is_component}})
        if liked and store_api_key:
            liked_filter = self.build_liked_filter()
            filter_conditions.append(liked_filter)
        elif liked and not store_api_key:
            msg = "You must provide an API key to filter by likes"
            raise APIKeyError(msg)

        if filter_by_user and store_api_key:
            user_data = user_data_var.get()
            if not user_data:
                msg = "No user data"
                raise ValueError(msg)
            filter_conditions.append({"user_created": {"_eq": user_data["id"]}})
        elif filter_by_user and not store_api_key:
            msg = "You must provide an API key to filter your components"
            raise APIKeyError(msg)
        else:
            filter_conditions.append({"private": {"_eq": False}})

        return filter_conditions

    @staticmethod
    def build_liked_filter():
        """构建点赞过滤条件。

        契约：无输入，输出过滤字典；副作用：读取 `user_data_var`。
        关键路径：使用 `directus_users_id` 与当前用户 `id`。
        异常流：缺少用户上下文时抛 `ValueError`。
        决策：依赖 `user_data_context` 提供用户信息
        问题：过滤条件需要当前用户 `id`
        方案：从 `ContextVar` 读取
        代价：未进入上下文会报错
        重评：若改为显式传入用户 `id`
        """
        user_data = user_data_var.get()
        # 注意：示例过滤条件用于按用户创建者筛选。
        if not user_data:
            msg = "No user data"
            raise ValueError(msg)
        return {"liked_by": {"directus_users_id": {"_eq": user_data["id"]}}}

    async def query_components(
        self,
        *,
        api_key: str | None = None,
        sort: list[str] | None = None,
        page: int = 1,
        limit: int = 15,
        fields: list[str] | None = None,
        filter_conditions: list[dict[str, Any]] | None = None,
        use_api_key: bool | None = False,
    ) -> tuple[list[ListComponentResponse], dict[str, Any]]:
        """查询组件列表并返回结果与元信息。

        契约：输入分页、排序与过滤条件，输出 `(results, meta)`；副作用：发起网络请求。
        关键路径（三步）：
        1) 构造 `fields/sort/filter` 参数。
        2) 根据 `use_api_key` 决定是否携带鉴权。
        3) 解析结果并转为 `ListComponentResponse`。
        异常流：网络错误或解析错误向上抛出。
        排障入口：`HTTP` 错误与上层日志。
        决策：仅在需要时传入 `API key`
        问题：避免无效 `API key` 导致 401
        方案：由 `use_api_key` 控制
        代价：部分私有数据需显式开启
        重评：若统一由服务端处理鉴权
        """
        params: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "fields": ",".join(fields) if fields is not None else ",".join(self.default_fields),
            "meta": "filter_count",  # 注意：`filter_count` 已废弃，需尽快移除。
        }
        # 注意：保留扩展位以支持聚合统计。

        if sort:
            params["sort"] = ",".join(sort)

        # 注意：默认仅查询公开组件或用户创建的组件。

        if filter_conditions:
            params["filter"] = json.dumps({"_and": filter_conditions})

        # 注意：未使用点赞过滤时可不传 `API key`，避免 401。
        api_key = api_key if use_api_key else None
        results, metadata = await self.get(self.components_url, api_key, params)
        if isinstance(results, dict):
            results = [results]

        results_objects = [ListComponentResponse(**result) for result in results]

        return results_objects, metadata

    async def get_liked_by_user_components(self, component_ids: list[str], api_key: str) -> list[str]:
        """获取用户已点赞的组件 ID 列表。

        契约：输入组件 ID 列表与 `API key`，输出已点赞的组件 ID；副作用：访问远程接口。
        关键路径：构造 `id in` 与 `liked_by` 的复合过滤条件。
        异常流：缺少用户上下文时抛 `ValueError`。
        决策：依赖 `user_data_context` 提供用户 ID
        问题：点赞关系需要当前用户信息
        方案：从 `user_data_var` 读取并构造过滤条件
        代价：上下文缺失会导致错误
        重评：若改为服务端直接返回点赞标记
        """
        # 注意：过滤条件包含组件 ID 集合与当前用户的点赞关系。
        user_data = user_data_var.get()
        if not user_data:
            msg = "No user data"
            raise ValueError(msg)
        params = {
            "fields": "id",
            "filter": json.dumps(
                {
                    "_and": [
                        {"id": {"_in": component_ids}},
                        {"liked_by": {"directus_users_id": {"_eq": user_data["id"]}}},
                    ]
                }
            ),
        }
        results, _ = await self.get(self.components_url, api_key, params)
        return [result["id"] for result in results]

    async def get_components_in_users_collection(self, component_ids: list[str], api_key: str):
        """获取用户集合中与给定组件相关的父组件 ID。

        契约：输入组件 ID 列表与 `API key`，输出父组件 ID 列表；副作用：访问远程接口。
        关键路径：过滤 `user_created` 与 `parent in component_ids`。
        异常流：缺少用户上下文时抛 `ValueError`。
        决策：依赖用户上下文来限定查询范围
        问题：需要仅返回当前用户集合的父组件
        方案：使用 `user_created` 过滤
        代价：上下文缺失会导致错误
        重评：若改为后端专用接口
        """
        user_data = user_data_var.get()
        if not user_data:
            msg = "No user data"
            raise ValueError(msg)
        params = {
            "fields": "id",
            "filter": json.dumps(
                {
                    "_and": [
                        {"user_created": {"_eq": user_data["id"]}},
                        {"parent": {"_in": component_ids}},
                    ]
                }
            ),
        }
        results, _ = await self.get(self.components_url, api_key, params)
        return [result["id"] for result in results]

    async def download(self, api_key: str, component_id: UUID) -> DownloadComponentResponse:
        """下载组件并补全元数据。

        契约：输入 `api_key/component_id`，输出 `DownloadComponentResponse`；副作用：访问远程接口并触发 `webhook`。
        关键路径（三步）：
        1) 拉取组件详情并触发下载 `webhook`。
        2) 构造响应模型并检查 `metadata`。
        3) 缺失 `metadata` 时基于 `nodes` 计算。
        异常流：组件数量异常或数据结构不合法时抛 `ValueError`。
        排障入口：错误信息 `Invalid component data`。
        决策：缺失元数据时在服务层补全
        问题：部分组件缺少 `metadata`
        方案：从 `data.nodes` 计算统计
        代价：下载流程增加一次处理
        重评：若上游保证 `metadata` 完整返回
        """
        url = f"{self.components_url}/{component_id}"
        params = {"fields": "id,name,description,data,is_component,metadata"}
        if not self.download_webhook_url:
            msg = "DOWNLOAD_WEBHOOK_URL is not set"
            raise ValueError(msg)
        component, _ = await self.get(url, api_key, params)
        await self.call_webhook(api_key, self.download_webhook_url, component_id)
        if len(component) > 1:
            msg = "Something went wrong while downloading the component"
            raise ValueError(msg)
        component_dict = component[0]

        download_component = DownloadComponentResponse(**component_dict)
        # 注意：当 `metadata` 为空时按 `nodes` 计算补全。
        if download_component.metadata in [None, {}] and download_component.data is not None:
            try:
                download_component.metadata = process_component_data(download_component.data.get("nodes", []))
            except KeyError as e:
                msg = "Invalid component data. No nodes found"
                raise ValueError(msg) from e
        return download_component

    async def upload(self, api_key: str, component_data: StoreComponentCreate) -> CreateComponentResponse:
        """上传新组件到 `Store`。

        契约：输入 `api_key` 与组件数据，输出 `CreateComponentResponse`；副作用：发起网络请求。
        关键路径（三步）：
        1) 序列化 `component_data` 并规范化 `parent/tags`。
        2) 发送 `POST` 请求创建组件。
        3) 解析响应并返回 `id`。
        异常流：`HTTPError` 时尝试解析错误消息并转为 `FilterError`。
        排障入口：错误信息 `Upload failed`。
        决策：在服务层处理 `Directus` 误报错误
        问题：`Directus` 返回通用错误难以定位
        方案：识别特定消息并替换为可操作提示
        代价：依赖上游错误文本稳定性
        重评：若上游提供结构化错误码
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        component_dict = component_data.model_dump(exclude_unset=True)
        # 注意：`parent` 在 `Store` 中要求为字符串。
        response = None
        if component_dict.get("parent"):
            component_dict["parent"] = str(component_dict["parent"])

        component_dict = process_tags_for_post(component_dict)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.components_url, headers=headers, json=component_dict, timeout=self.timeout
                )
                response.raise_for_status()
            component = response.json()["data"]
            return CreateComponentResponse(**component)
        except HTTPError as exc:
            if response:
                try:
                    errors = response.json()
                    message = errors["errors"][0]["message"]
                    if message == "An unexpected error occurred.":
                        # 注意：`Directus` 误报通用错误，需转换为可操作提示。
                        message = "You already have a component with this name. Please choose a different name."
                    raise FilterError(message)
                except UnboundLocalError:
                    pass
            msg = f"Upload failed: {exc}"
            raise ValueError(msg) from exc

    async def update(
        self, api_key: str, component_id: UUID, component_data: StoreComponentCreate
    ) -> CreateComponentResponse:
        """更新已有组件。

        契约：输入 `api_key/component_id/component_data`，输出 `CreateComponentResponse`；副作用：发起网络请求。
        关键路径（三步）：
        1) 序列化 `component_data` 并规范化 `parent/tags`。
        2) 发送 `PATCH` 请求更新组件。
        3) 解析响应并返回 `id`。
        异常流：`HTTPError` 时尝试解析错误消息并转为 `FilterError`。
        排障入口：错误信息 `Upload failed`。
        决策：复用上传的错误处理逻辑
        问题：更新接口同样返回 `Directus` 通用错误
        方案：按同样规则改写错误信息
        代价：重复依赖上游错误文本
        重评：若上游提供稳定错误码
        """
        # 注意：`PATCH` 与 `POST` 类似，但需在 `URL` 中附带 `component_id`。
        headers = {"Authorization": f"Bearer {api_key}"}
        component_dict = component_data.model_dump(exclude_unset=True)
        # 注意：`parent` 在 `Store` 中要求为字符串。
        response = None
        if component_dict.get("parent"):
            component_dict["parent"] = str(component_dict["parent"])

        component_dict = process_tags_for_post(component_dict)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    self.components_url + f"/{component_id}", headers=headers, json=component_dict, timeout=self.timeout
                )
                response.raise_for_status()
            component = response.json()["data"]
            return CreateComponentResponse(**component)
        except HTTPError as exc:
            if response:
                try:
                    errors = response.json()
                    message = errors["errors"][0]["message"]
                    if message == "An unexpected error occurred.":
                        # 注意：`Directus` 误报通用错误，需转换为可操作提示。
                        message = "You already have a component with this name. Please choose a different name."
                    raise FilterError(message)
                except UnboundLocalError:
                    pass
            msg = f"Upload failed: {exc}"
            raise ValueError(msg) from exc

    async def get_tags(self) -> list[dict[str, Any]]:
        """获取标签列表。

        契约：无输入，输出标签字典列表；副作用：访问远程接口。
        关键路径：调用 `tags` 端点并返回 `data`。
        决策：仅请求 `id/name` 字段
        问题：减少响应体大小
        方案：通过 `fields` 参数限制字段
        代价：若需要更多字段需额外查询
        重评：当标签展示需求扩展
        """
        url = f"{self.base_url}/items/tags"
        params = {"fields": "id,name"}
        tags, _ = await self.get(url, api_key=None, params=params)
        return tags

    async def get_user_likes(self, api_key: str) -> list[dict[str, Any]]:
        """获取用户点赞信息。

        契约：输入 `api_key`，输出点赞数据列表；副作用：访问远程接口。
        关键路径：调用 `users/me` 并限制字段为 `id/likes`。
        决策：最小字段集合返回
        问题：避免返回多余用户信息
        方案：通过 `fields` 过滤
        代价：需要更多信息时需额外请求
        重评：若接口需要更多用户属性
        """
        url = f"{self.base_url}/users/me"
        params = {
            "fields": "id,likes",
        }
        likes, _ = await self.get(url, api_key, params)
        return likes

    async def get_component_likes_count(self, component_id: str, api_key: str | None = None) -> int:
        """获取组件点赞数量。

        契约：输入 `component_id` 与可选 `api_key`，输出点赞数；副作用：访问远程接口。
        关键路径：获取 `count(liked_by)` 并转换为整数。
        异常流：无结果或数值不可转换时抛 `ValueError`。
        排障入口：错误信息 `Unexpected value for likes count`。
        决策：将返回值统一转换为 `int`
        问题：上游返回值为字符串
        方案：强制 `int()` 转换
        代价：异常时需要额外处理
        重评：若上游改为数值类型
        """
        url = f"{self.components_url}/{component_id}"

        params = {
            "fields": "id,count(liked_by)",
        }
        result, _ = await self.get(url, api_key=api_key, params=params)
        if len(result) == 0:
            msg = "Component not found"
            raise ValueError(msg)
        likes = result[0]["liked_by_count"]
        # 注意：`liked_by_count` 为字符串，需要转换为整数。
        try:
            likes = int(likes)
        except ValueError as e:
            msg = f"Unexpected value for likes count: {likes}"
            raise ValueError(msg) from e
        return likes

    async def like_component(self, api_key: str, component_id: str) -> bool:
        """点赞或取消点赞组件。

        契约：输入 `api_key/component_id`，输出布尔值；副作用：调用点赞 `webhook`。
        关键路径：发送 `POST` 并根据返回类型判断点赞状态。
        异常流：状态码非 200 或返回结构异常时抛 `ValueError`。
        排障入口：错误信息 `Unexpected status code`。
        决策：通过返回值类型区分点赞/取消
        问题：`webhook` 返回结构不统一
        方案：列表表示点赞成功，整数表示取消
        代价：对返回类型强依赖
        重评：若 `webhook` 返回结构标准化
        """
        if not self.like_webhook_url:
            msg = "LIKE_WEBHOOK_URL is not set"
            raise ValueError(msg)
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.like_webhook_url,
                json={"component_id": str(component_id)},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        if response.status_code == httpx.codes.OK:
            result = response.json()

            if isinstance(result, list):
                return True
            if isinstance(result, int):
                return False
            msg = f"Unexpected result: {result}"
            raise ValueError(msg)
        msg = f"Unexpected status code: {response.status_code}"
        raise ValueError(msg)

    async def get_list_component_response_model(
        self,
        *,
        component_id: str | None = None,
        search: str | None = None,
        private: bool | None = None,
        tags: list[str] | None = None,
        is_component: bool | None = None,
        fields: list[str] | None = None,
        filter_by_user: bool = False,
        liked: bool = False,
        store_api_key: str | None = None,
        sort: list[str] | None = None,
        page: int = 1,
        limit: int = 15,
    ):
        """构建组件列表响应模型。

        契约：输入筛选参数与分页信息，输出 `ListComponentResponseModel`；副作用：访问远程接口。
        关键路径（三步）：
        1) 构建过滤条件并查询组件列表。
        2) 计算总数并处理鉴权错误。
        3) 在需要时补充用户点赞/授权信息。
        异常流：`HTTPStatusError` 转为 `APIKeyError/ForbiddenError`。
        排障入口：错误信息 `You are not authorized`。
        决策：缺少 `meta` 时自行计算数量
        问题：`meta` 可能不返回或已废弃
        方案：根据结果长度或二次计数补全
        代价：可能触发额外请求
        重评：若 `Store` 提供稳定计数
        """
        async with user_data_context(api_key=store_api_key, store_service=self):
            filter_conditions: list[dict[str, Any]] = self.build_filter_conditions(
                component_id=component_id,
                search=search,
                private=private,
                tags=tags,
                is_component=is_component,
                filter_by_user=filter_by_user,
                liked=liked,
                store_api_key=store_api_key,
            )

            result: list[ListComponentResponse] = []
            authorized = False
            metadata: dict = {}
            comp_count = 0
            try:
                result, metadata = await self.query_components(
                    api_key=store_api_key,
                    page=page,
                    limit=limit,
                    sort=sort,
                    fields=fields,
                    filter_conditions=filter_conditions,
                    use_api_key=liked or filter_by_user,
                )
                if metadata:
                    comp_count = metadata.get("filter_count", 0)
            except HTTPStatusError as exc:
                if exc.response.status_code == httpx.codes.FORBIDDEN:
                    msg = "You are not authorized to access this public resource"
                    raise ForbiddenError(msg) from exc
                if exc.response.status_code == httpx.codes.UNAUTHORIZED:
                    msg = "You are not authorized to access this resource. Please check your API key."
                    raise APIKeyError(msg) from exc
            except Exception as exc:
                msg = f"Unexpected error: {exc}"
                raise ValueError(msg) from exc
            try:
                if result and not metadata:
                    if len(result) >= limit:
                        comp_count = await self.count_components(
                            api_key=store_api_key,
                            filter_conditions=filter_conditions,
                            use_api_key=liked or filter_by_user,
                        )
                    else:
                        comp_count = len(result)
                elif not metadata:
                    comp_count = 0
            except HTTPStatusError as exc:
                if exc.response.status_code == httpx.codes.FORBIDDEN:
                    msg = "You are not authorized to access this public resource"
                    raise ForbiddenError(msg) from exc
                if exc.response.status_code == httpx.codes.UNAUTHORIZED:
                    msg = "You are not authorized to access this resource. Please check your API key."
                    raise APIKeyError(msg) from exc

            if store_api_key:
                # 注意：补充用户点赞信息，若缺少组件 `id` 则回退为仅鉴权判断。

                if not result or any(component.id is None for component in result):
                    authorized = await self.check_api_key(store_api_key)
                else:
                    try:
                        updated_result = await update_components_with_user_data(
                            result, self, store_api_key, liked=liked
                        )
                        authorized = True
                        result = updated_result
                    except Exception:  # noqa: BLE001
                        logger.debug("Error updating components with user data", exc_info=True)
                        # 注意：此处异常通常意味着用户未被授权。
                        authorized = False
        return ListComponentResponseModel(results=result, authorized=authorized, count=comp_count)
