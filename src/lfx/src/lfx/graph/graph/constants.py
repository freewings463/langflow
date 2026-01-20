"""模块名称：图运行时常量与顶点类型映射

本模块定义图运行时的轻量常量与顶点类型懒加载映射。
使用场景：在不引入重依赖的前提下获取顶点类型与完成标记。
主要功能包括：
- 提供 `Finish` 标记用于流程结束判定
- 懒加载顶点类型映射，避免循环依赖

关键组件：
- Finish：结束标记对象
- VertexTypesDict：顶点类型字典与懒加载入口
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lfx.graph.schema import CHAT_COMPONENTS

if TYPE_CHECKING:
    from lfx.graph.vertex.base import Vertex
    from lfx.graph.vertex.vertex_types import CustomComponentVertex


class Finish:
    """流程结束标记。

    契约：仅用于布尔判断与类型比较，不承载业务状态
    """
    def __bool__(self) -> bool:
        return True

    def __eq__(self, /, other):
        return isinstance(other, Finish)

    def __hash__(self) -> int:
        return hash(type(self))


def _import_vertex_types():
    """延迟导入顶点类型模块，避免循环依赖。"""
    from lfx.graph.vertex import vertex_types

    return vertex_types


class VertexTypesDict:
    """顶点类型字典的懒加载包装。"""

    def __init__(self) -> None:
        self._all_types_dict = None
        self._types = _import_vertex_types

    @property
    def all_types_dict(self):
        """返回完整类型映射，首次访问时构建。"""
        if self._all_types_dict is None:
            self._all_types_dict = self._build_dict()
        return self._all_types_dict

    @property
    def vertex_type_map(self) -> dict[str, type[Vertex]]:
        """对外暴露的类型映射入口。"""
        return self.all_types_dict

    def _build_dict(self):
        """构建类型映射，合并内置与自定义组件类型。"""
        langchain_types_dict = self.get_type_dict()
        return {
            **langchain_types_dict,
            "Custom": ["Custom Tool", "Python Function"],
        }

    def get_type_dict(self) -> dict[str, type[Vertex]]:
        """获取基础顶点类型映射。"""
        types = self._types()
        return {
            "CustomComponent": types.CustomComponentVertex,
            "Component": types.ComponentVertex,
            **dict.fromkeys(CHAT_COMPONENTS, types.InterfaceVertex),
        }

    def get_custom_component_vertex_type(self) -> type[CustomComponentVertex]:
        """返回自定义组件顶点类型。"""
        return self._types().CustomComponentVertex


lazy_load_vertex_dict = VertexTypesDict()
