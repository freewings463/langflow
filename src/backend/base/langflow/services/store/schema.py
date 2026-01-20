"""
模块名称：`Store` 服务数据模型

本模块定义 `Store` 服务对外返回与入参的 `Pydantic` 模型，主要用于约束 `API` 响应结构。
主要功能包括：
- `Store` 组件列表、下载、创建等场景的响应模型。
- 标签与用户点赞统计的结构化表示。

关键组件：`ListComponentResponse`、`ListComponentResponseModel`、`StoreComponentCreate`。
设计背景：统一 `Store` 接口的序列化/反序列化结构。
使用场景：`Store` 服务与 `API` 层交互的数据校验。
注意事项：字段默认值应与上游 `Store` 接口保持一致。
"""

from uuid import UUID

from pydantic import BaseModel, field_validator


class TagResponse(BaseModel):
    """标签响应模型。

    契约：包含 `id/name`；输出为可序列化对象；副作用：无。
    关键路径：用于列表与标签关联展示。
    决策：`name` 允许为空以兼容缺失数据
    问题：历史数据可能缺少标签名
    方案：`name` 设为可选
    代价：调用方需处理空值
    重评：当上游确保标签完整时
    """

    id: UUID
    name: str | None


class UsersLikesResponse(BaseModel):
    """用户点赞信息响应模型。

    契约：包含 `likes_count/liked_by_user`；副作用：无。
    关键路径：用于列表页面点赞状态渲染。
    决策：字段允许为空
    问题：部分接口不返回完整统计
    方案：使用可选字段
    代价：前端需做空值兼容
    重评：若统计字段强制返回
    """

    likes_count: int | None
    liked_by_user: bool | None


class CreateComponentResponse(BaseModel):
    """组件创建响应模型。

    契约：包含新组件 `id`；副作用：无。
    关键路径：用于创建成功后的跳转与后续调用。
    决策：仅返回 `id`
    问题：创建接口需要轻量响应
    方案：最小化返回字段
    代价：调用方需二次查询详情
    重评：若需要返回更多创建元信息
    """

    id: UUID


class TagsIdResponse(BaseModel):
    """标签关联结构响应模型。

    契约：字段 `tags_id` 可能为空；副作用：无。
    关键路径：用于兼容 `Store` 返回的嵌套结构。
    决策：保留嵌套结构以适配上游数据
    问题：上游返回 `tags_id` 包裹对象
    方案：提供中间模型承接
    代价：调用方需做二次转换
    重评：当上游结构简化时
    """

    tags_id: TagResponse | None


class ListComponentResponse(BaseModel):
    """组件列表项响应模型。

    契约：字段为可选，以兼容部分字段缺失；副作用：无。
    关键路径：`tags` 需在校验器中转换为 `TagResponse` 列表。
    决策：允许 `metadata/user_created` 默认为空字典
    问题：上游可能返回空对象或缺失字段
    方案：提供默认空对象以避免 `None` 判空
    代价：可变默认值需谨慎使用
    重评：若改为不可变默认或显式 `None`
    """

    id: UUID | None = None
    name: str | None = None
    description: str | None = None
    liked_by_count: int | None = None
    liked_by_user: bool | None = None
    is_component: bool | None = None
    metadata: dict | None = {}
    user_created: dict | None = {}
    tags: list[TagResponse] | None = None
    downloads_count: int | None = None
    last_tested_version: str | None = None
    private: bool | None = None

    # 注意：`tags` 可能是 `TagsIdResponse` 列表，需转换为 `TagResponse` 列表。
    @field_validator("tags", mode="before")
    @classmethod
    def tags_to_list(cls, v):
        """将 `tags` 输入规范化为 `TagResponse` 列表。

        契约：输入原始 `tags` 值，输出 `TagResponse` 列表或原值。
        关键路径：检测是否已包含 `id/name`，否则从 `tags_id` 抽取。
        决策：优先保留已规范化的结构
        问题：上游返回结构存在两种格式
        方案：在校验阶段统一格式
        代价：校验逻辑耦合上游结构
        重评：若上游统一返回格式
        """
        # 注意：若已包含 `id/name`，直接返回原值。
        if not v:
            return v
        if all("id" in tag and "name" in tag for tag in v):
            return v
        return [TagResponse(**tag.get("tags_id")) for tag in v if tag.get("tags_id")]


class ListComponentResponseModel(BaseModel):
    """组件列表响应模型。

    契约：包含 `count/authorized/results`；副作用：无。
    关键路径：`authorized` 表示调用方鉴权结果。
    决策：`count` 默认为 0 便于分页渲染
    问题：部分场景无 `meta` 返回
    方案：以默认值兜底
    代价：需区分真实 0 与未知
    重评：若统一返回 `meta` 计数
    """

    count: int | None = 0
    authorized: bool
    results: list[ListComponentResponse] | None


class DownloadComponentResponse(BaseModel):
    """组件下载响应模型。

    契约：包含组件基础信息与 `data/metadata`；副作用：无。
    关键路径：`metadata` 允许为空，下载流程可能补充。
    决策：`metadata` 默认空字典以便补全
    问题：下载接口可能不返回完整元数据
    方案：默认空字典并在服务层填充
    代价：需在服务层执行额外处理
    重评：若上游保证返回元数据
    """

    id: UUID
    name: str | None
    description: str | None
    data: dict | None
    is_component: bool | None
    metadata: dict | None = {}


class StoreComponentCreate(BaseModel):
    """组件创建入参模型。

    契约：`name/data` 必填，其余可选；副作用：无。
    关键路径：`parent` 与 `tags` 会在服务层转换。
    决策：`private` 默认 `True` 以降低误公开风险
    问题：新组件默认应为私有
    方案：在模型层设置默认值
    代价：需要显式打开公开选项
    重评：若产品默认策略调整
    """

    name: str
    description: str | None
    data: dict
    tags: list[str] | None
    parent: UUID | None = None
    is_component: bool | None
    last_tested_version: str | None = None
    private: bool | None = True
