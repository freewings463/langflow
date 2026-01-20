"""
模块名称：组件类型列表（懒加载）

本模块提供组件类型的延迟加载字典，用于前端展示与选择。主要功能包括：
- 合并内置类型与自定义类型
- 延迟构建类型字典以减少启动成本

关键组件：
- `AllTypesDict`：基于 `LazyLoadDictBase` 的懒加载实现

设计背景：避免启动时加载全部组件，提高响应速度。
使用场景：UI 展示组件类型或下拉选项时。
注意事项：首次访问会触发实际加载。
"""

from typing_extensions import override

from lfx.services.deps import get_settings_service
from lfx.utils.lazy_load import LazyLoadDictBase


class AllTypesDict(LazyLoadDictBase):
    """组件类型懒加载字典。

    契约：首次访问时构建类型字典；后续从缓存返回。
    副作用：触发设置服务读取与组件类型构建。
    失败语义：加载失败时异常上抛。
    决策：通过 LazyLoadDictBase 延迟构建。
    问题：全量加载类型信息会影响启动性能。
    方案：仅在使用时构建字典。
    代价：首次访问存在额外延迟。
    重评：当启动性能足够或缓存命中稳定时可预加载。
    """

    def __init__(self) -> None:
        """初始化懒加载容器。"""
        self._all_types_dict = None

    def _build_dict(self):
        """构建包含内置与自定义的类型字典。

        契约：返回包含所有类型的字典。
        副作用：触发 `get_type_dict` 加载。
        失败语义：加载失败时异常上抛。
        决策：追加固定的 Custom 类型。
        问题：部分自定义工具不属于常规模块类型。
        方案：在类型字典中追加 "Custom" 类别。
        代价：类型集合与真实组件不完全一致。
        重评：当 Custom 有专属类型定义时移除硬编码。
        """
        langchain_types_dict = self.get_type_dict()
        return {
            **langchain_types_dict,
            "Custom": ["Custom Tool", "Python Function"],
        }

    @override
    def get_type_dict(self):
        """获取所有组件类型字典。

        契约：返回按类型组织的组件字典。
        副作用：读取 settings_service 配置。
        失败语义：设置服务异常将上抛。
        决策：由 settings_service 提供组件路径。
        问题：组件路径在运行时可配置。
        方案：从 settings_service 读取路径后加载。
        代价：依赖运行时配置服务可用性。
        重评：当路径固定或可内置时减少依赖。
        """
        from lfx.custom.utils import get_all_types_dict

        settings_service = get_settings_service()
        return get_all_types_dict(settings_service.settings.components_path)


lazy_load_dict = AllTypesDict()
