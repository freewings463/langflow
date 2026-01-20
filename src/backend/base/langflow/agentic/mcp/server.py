"""
模块名称：Langflow Agentic `MCP` 服务工具

本模块提供 `MCP` 工具注册与实现，主要用于对外暴露模板检索/创建、组件检索、`Flow` 图可视化与组件字段读写能力。主要功能包括：
- 模板查询与基于模板创建 `Flow`
- 组件索引检索与字段读取/更新
- `Flow` 图结构的 `ASCII`/文本表示与摘要

关键组件：
- `mcp`：`FastMCP` 服务器实例，作为工具注册入口
- 各 `@mcp.tool()` 函数：`MCP` 工具实现

设计背景：为 Agentic 场景提供统一 `MCP` 接口，避免外部直接耦合内部服务层实现
注意事项：大多数工具为只读；写操作仅限明确接口且异常通常原样抛出或以 `error` 字段返回
"""

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from langflow.agentic.mcp.support import replace_none_and_null_with_empty_str
from langflow.agentic.utils.component_search import (
    get_all_component_types,
    get_component_by_name,
    get_components_by_type,
    get_components_count,
    list_all_components,
)
from langflow.agentic.utils.flow_component import (
    get_component_details,
    get_component_field_value,
    list_component_fields,
    update_component_field_value,
)
from langflow.agentic.utils.flow_graph import (
    get_flow_ascii_graph,
    get_flow_graph_representations,
    get_flow_graph_summary,
    get_flow_text_repr,
)
from langflow.agentic.utils.template_create import (
    create_flow_from_template_and_get_link,
)
from langflow.agentic.utils.template_search import (
    get_all_tags,
    get_template_by_id,
    get_templates_count,
    list_templates,
)
from langflow.services.deps import get_settings_service, session_scope

# 实现：工具注册依赖单例 `mcp`，模块导入即完成注册
mcp = FastMCP("langflow-agentic")

DEFAULT_TEMPLATE_FIELDS = ["id", "name", "description", "tags", "endpoint_name", "icon"]
DEFAULT_COMPONENT_FIELDS = ["name", "type", "display_name", "description"]


@mcp.tool()
def search_templates(query: str | None = None, fields: list[str] = DEFAULT_TEMPLATE_FIELDS) -> list[dict[str, Any]]:
    """按关键词检索模板并裁剪字段。

    契约：输入 `query`/`fields`；输出模板字段列表；只读。
    关键路径：1) 兜底 `fields` 2) 调用 `list_templates` 返回结果。
    失败语义：模板索引读取异常原样抛出。
    决策：默认裁剪字段集合
    问题：模板完整 `JSON` 体积较大，影响 `MCP` 传输与 `LLM` 负载
    方案：缺省返回 `DEFAULT_TEMPLATE_FIELDS`，允许调用方覆盖
    代价：需要全量字段时需显式传 `fields`
    重评：当常见调用都需要完整模板时
    """
    if fields is None:
        fields = DEFAULT_TEMPLATE_FIELDS
    return list_templates(query=query, fields=fields)


@mcp.tool()
def get_template(
    template_id: str,
    fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """按模板 ID 获取模板数据。

    契约：输入 `template_id`/可选 `fields`；输出模板 `dict` 或 `None`；只读。
    关键路径：1) 透传参数 2) 调用 `get_template_by_id`。
    失败语义：未命中返回 `None`；上游读取异常原样抛出。
    决策：保留 `fields=None` 表示返回全字段
    问题：不同调用方字段需求差异大
    方案：空字段列表不设默认裁剪
    代价：可能返回较大 `payload`
    重评：当全量返回成为性能瓶颈时
    """
    return get_template_by_id(template_id=template_id, fields=fields)


@mcp.tool()
def list_all_tags() -> list[str]:
    """列出模板使用过的全部标签。

    契约：无输入；输出去重且排序后的标签列表；只读。
    关键路径：调用 `get_all_tags` 汇总标签并排序。
    失败语义：上游读取异常按其实现处理（可能记录日志或抛出）。
    决策：提供稳定排序输出
    问题：无序列表会导致 `MCP` 响应不稳定
    方案：上游统一排序并返回
    代价：排序带来微小 `CPU` 开销
    重评：当标签量级显著增长且需分页时
    """
    return get_all_tags()


@mcp.tool()
def count_templates() -> int:
    """统计模板总数。

    契约：无输入；输出模板数量；只读。
    关键路径：调用 `get_templates_count` 统计模板文件。
    失败语义：上游文件系统异常原样抛出。
    决策：以文件计数作为权威来源
    问题：模板来源为本地 `starter_projects` 文件集
    方案：直接统计 `JSON` 文件数量
    代价：每次调用都会触发目录扫描
    重评：当模板迁移到数据库或索引服务时
    """
    return get_templates_count()


# 模板创建工具
@mcp.tool()
async def create_flow_from_template(
    template_id: str,
    user_id: str,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """基于模板创建 `Flow` 并返回 ID 与 `UI` 链接。

    契约：输入 `template_id`/`user_id`/`folder_id`；输出 `{id, link}`；副作用为数据库写入。
    关键路径：1) 进入 `session_scope` 2) 解析 `UUID` 3) 调用创建逻辑返回结果。
    失败语义：`UUID` 非法抛 `ValueError`；上游创建失败异常原样抛出。
    决策：工具层直接返回 `UI` 链接
    问题：调用方需要创建后立即跳转/访问
    方案：创建完成后返回 `id` + `link`
    代价：接口绑定 `UI` 路径语义
    重评：当 `UI` 路由或访问策略调整时
    """
    async with session_scope() as session:
        return await create_flow_from_template_and_get_link(
            session=session,
            user_id=UUID(user_id),
            template_id=template_id,
            target_folder_id=UUID(folder_id) if folder_id else None,
        )


# 组件检索工具
@mcp.tool()
async def search_components(
    query: str | None = None,
    component_type: str | None = None,
    fields: list[str] | None = None,
    *,
    add_search_text: bool | None = None,
) -> list[dict[str, Any]]:
    """检索组件并可附加检索文本字段。

    契约：输入 `query`/`component_type`/`fields`/`add_search_text`；输出组件列表；空值/缺失字段统一为 `Not available`；只读。
    关键路径：1) 兜底默认参数 2) 拉取组件列表 3) 追加 `text` 并做空值归一化。
    失败语义：上游组件加载异常原样抛出。
    决策：默认补充 `text` 字段用于检索
    问题：检索端需要单字段文本进行匹配
    方案：将每条记录的键值拼接为 `text`
    代价：返回体体积增大
    重评：当调用方更偏好结构化字段时
    """
    if add_search_text is None:
        add_search_text = True
    if fields is None:
        fields = DEFAULT_COMPONENT_FIELDS

    settings_service = get_settings_service()
    result = await list_all_components(
        query=query,
        component_type=component_type,
        fields=fields,
        settings_service=settings_service,
    )
    if add_search_text:
        for comp in result:
            text_lines = [f"{k} {v}" for k, v in comp.items() if k != "text"]
            comp["text"] = "\n".join(text_lines)
    return replace_none_and_null_with_empty_str(result, required_fields=fields)


@mcp.tool()
async def get_component(
    component_name: str,
    component_type: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """按组件名获取组件信息。

    契约：输入 `component_name`/`component_type`/`fields`；输出组件 `dict` 或 `None`；只读。
    关键路径：1) 透传查询参数 2) 调用 `get_component_by_name`。
    失败语义：未命中返回 `None`；上游读取异常原样抛出。
    决策：提供 `component_type` 限定
    问题：同名组件可能跨类型存在
    方案：允许调用方传类型缩小范围
    代价：需要调用方了解类型枚举
    重评：当组件命名规则稳定且无歧义时
    """
    settings_service = get_settings_service()
    return await get_component_by_name(
        component_name=component_name,
        component_type=component_type,
        fields=fields,
        settings_service=settings_service,
    )


@mcp.tool()
async def list_component_types() -> list[str]:
    """列出可用组件类型。

    契约：无输入；输出组件类型列表；只读。
    关键路径：获取 `settings_service` 后调用 `get_all_component_types`。
    失败语义：配置加载或上游查询异常原样抛出。
    决策：每次调用实时读取配置
    问题：组件类型可能由配置动态扩展
    方案：从 `settings_service` 拉取最新配置
    代价：调用成本高于静态缓存
    重评：当类型列表长期稳定且调用频繁时
    """
    settings_service = get_settings_service()
    return await get_all_component_types(settings_service=settings_service)


@mcp.tool()
async def count_components(component_type: str | None = None) -> int:
    """统计组件数量，可按类型过滤。

    契约：输入可选 `component_type`；输出组件数量；只读。
    关键路径：获取 `settings_service` 后调用 `get_components_count`。
    失败语义：上游统计异常原样抛出。
    决策：统计逻辑下沉到服务层
    问题：组件来源与配置关联，需统一统计口径
    方案：复用内部统计函数
    代价：无法在此层做额外聚合
    重评：当需要按更多维度统计时
    """
    settings_service = get_settings_service()
    return await get_components_count(component_type=component_type, settings_service=settings_service)


@mcp.tool()
async def get_components_by_type_tool(
    component_type: str,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按类型批量获取组件列表。

    契约：输入 `component_type`/`fields`；输出组件列表；只读。
    关键路径：1) 兜底 `fields` 2) 调用 `get_components_by_type`。
    失败语义：上游查询异常原样抛出。
    决策：默认字段裁剪以控制返回体积
    问题：完整组件定义包含大字段（如 `template`）
    方案：缺省使用 `DEFAULT_COMPONENT_FIELDS`
    代价：调用方需显式请求额外字段
    重评：当默认字段无法满足多数场景时
    """
    if fields is None:
        fields = DEFAULT_COMPONENT_FIELDS

    settings_service = get_settings_service()
    return await get_components_by_type(
        component_type=component_type,
        fields=fields,
        settings_service=settings_service,
    )


# `Flow` 图可视化工具
@mcp.tool()
async def visualize_flow_graph(
    flow_id_or_name: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """获取 `Flow` 图的 `ASCII`+文本双视图。

    契约：输入 `flow_id_or_name`/`user_id`；输出包含 `ascii_graph`/`text_repr`/统计信息的 `dict`；失败时返回含 `error` 的 `dict`。
    关键路径：调用 `get_flow_graph_representations` 聚合视图。
    失败语义：`Flow` 不存在或无数据时返回 `error` 字段；异常以 `error` 返回。
    决策：单次调用返回两种视图
    问题：分开调用会增加 `RPC` 与数据库读取开销
    方案：上游一次性生成两种表示
    代价：返回体更大
    重评：当客户端长期只需要单一视图时
    """
    return await get_flow_graph_representations(flow_id_or_name, user_id)


@mcp.tool()
async def get_flow_ascii_diagram(
    flow_id_or_name: str,
    user_id: str | None = None,
) -> str:
    """获取 `Flow` 的 `ASCII` 图。

    契约：输入 `flow_id_or_name`/`user_id`；输出 `ASCII` 字符串；失败时返回以 `Error:` 开头的字符串或无图提示。
    关键路径：调用 `get_flow_ascii_graph`。
    失败语义：上游返回 `error` 时转换为文本错误提示。
    决策：保持字符串输出而非结构化对象
    问题：`MCP` 消费端需要可直接渲染的文本
    方案：统一返回 `ASCII` 文本
    代价：缺少结构化错误码
    重评：当客户端需要结构化错误时
    """
    return await get_flow_ascii_graph(flow_id_or_name, user_id)


@mcp.tool()
async def get_flow_text_representation(
    flow_id_or_name: str,
    user_id: str | None = None,
) -> str:
    """获取 `Flow` 图的文本结构表示。

    契约：输入 `flow_id_or_name`/`user_id`；输出文本表示字符串；失败时返回以 `Error:` 开头的字符串或无文本提示。
    关键路径：调用 `get_flow_text_repr`。
    失败语义：上游返回 `error` 时转换为文本错误提示。
    决策：以文本形式暴露顶点/边信息
    问题：调试和解释需要可读文本
    方案：复用上游 `repr` 结果
    代价：难以被程序化解析
    重评：当客户端需要结构化图数据时
    """
    return await get_flow_text_repr(flow_id_or_name, user_id)


@mcp.tool()
async def get_flow_structure_summary(
    flow_id_or_name: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """获取 `Flow` 结构摘要（不含图形视图）。

    契约：输入 `flow_id_or_name`/`user_id`；输出结构统计 `dict`；失败时返回含 `error` 的 `dict`。
    关键路径：调用 `get_flow_graph_summary` 获取节点/边摘要。
    失败语义：`Flow` 不存在或无数据时返回 `error`；异常以 `error` 返回。
    决策：摘要接口不返回 `ASCII`/文本图
    问题：部分调用只需结构统计，不需要完整视图
    方案：只返回计数与节点/边列表
    代价：无法直接展示可视化结构
    重评：当多数调用需要可视化时
    """
    return await get_flow_graph_summary(flow_id_or_name, user_id)


# `Flow` 组件字段工具
@mcp.tool()
async def get_flow_component_details(
    flow_id_or_name: str,
    component_id: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """获取 `Flow` 内指定组件的详细信息。

    契约：输入 `flow_id_or_name`/`component_id`/`user_id`；输出包含组件模板与连线信息的 `dict`；失败时返回含 `error` 的 `dict`。
    关键路径：调用 `get_component_details` 读取组件与连线信息。
    失败语义：`Flow`/组件不存在时返回 `error` 字段；异常以 `error` 返回。
    决策：返回完整模板与连线数据
    问题：编辑与排障需要完整字段与连接关系
    方案：透传上游完整节点信息
    代价：返回体积较大
    重评：当仅需摘要字段时
    """
    return await get_component_details(flow_id_or_name, component_id, user_id)


@mcp.tool()
async def get_flow_component_field_value(
    flow_id_or_name: str,
    component_id: str,
    field_name: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """获取 `Flow` 组件字段的当前值与配置。

    契约：输入 `flow_id_or_name`/`component_id`/`field_name`/`user_id`；输出字段信息 `dict`；失败时返回含 `error` 的 `dict`。
    关键路径：调用 `get_component_field_value` 读取字段。
    失败语义：字段不存在时返回 `error` 且包含 `available_fields`；异常以 `error` 返回。
    决策：返回字段配置而非仅返回值
    问题：调用方往往需要 `field_type`/`required` 等元信息
    方案：透传上游字段配置
    代价：响应体更大
    重评：当调用方仅需值时
    """
    return await get_component_field_value(flow_id_or_name, component_id, field_name, user_id)


@mcp.tool()
async def update_flow_component_field(
    flow_id_or_name: str,
    component_id: str,
    field_name: str,
    new_value: str,
    user_id: str,
) -> dict[str, Any]:
    """更新 `Flow` 组件字段并持久化到数据库。

    契约：输入 `flow_id_or_name`/`component_id`/`field_name`/`new_value`/`user_id`；输出含 `success` 的结果 `dict`；副作用为数据库写入。
    关键路径：调用 `update_component_field_value` 完成定位、写入与提交。
    失败语义：`Flow`/组件/字段不存在或权限不匹配时返回 `success=False` 且包含 `error`；异常以 `error` 返回。
    决策：强制要求 `user_id` 参与授权校验
    问题：字段更新属于高风险写操作
    方案：在写入前校验 `Flow` 所属用户
    代价：调用方必须提供有效 `user_id`
    重评：当引入服务端身份上下文或统一鉴权中间件时
    """
    return await update_component_field_value(flow_id_or_name, component_id, field_name, new_value, user_id)


@mcp.tool()
async def list_flow_component_fields(
    flow_id_or_name: str,
    component_id: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """列出组件全部字段及当前值。

    契约：输入 `flow_id_or_name`/`component_id`/`user_id`；输出字段字典与 `field_count`；失败时返回含 `error` 的 `dict`。
    关键路径：调用 `list_component_fields` 汇总字段信息。
    失败语义：`Flow`/组件不存在时返回 `error`；异常以 `error` 返回。
    决策：返回 `field_count` 便于快速判断字段规模
    问题：调用方需要在 `UI` 中快速显示字段数量
    方案：在结果中附带字段计数
    代价：需一次遍历统计字段数
    重评：当字段数量统计不再被消费时
    """
    return await list_component_fields(flow_id_or_name, component_id, user_id)


if __name__ == "__main__":
    mcp.run()
