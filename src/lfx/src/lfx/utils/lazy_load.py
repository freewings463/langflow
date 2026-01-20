"""模块名称：惰性字典构建基类

模块目的：提供惰性初始化的字典访问模式。
主要功能：按需构建并缓存类型字典。
使用场景：类型映射构建开销较大且不总是需要的场合。
关键组件：`LazyLoadDictBase`
设计背景：避免启动时构建大型映射，降低冷启动成本。
注意事项：子类必须实现 `_build_dict` 与 `get_type_dict`。
"""


class LazyLoadDictBase:
    """惰性字典构建的基类模板。"""

    def __init__(self) -> None:
        self._all_types_dict = None

    @property
    def all_types_dict(self):
        """首次访问时构建字典并缓存。"""
        if self._all_types_dict is None:
            self._all_types_dict = self._build_dict()
        return self._all_types_dict

    def _build_dict(self):
        """子类实现：构建完整字典。"""
        raise NotImplementedError

    def get_type_dict(self):
        """子类实现：按类型返回子字典。"""
        raise NotImplementedError
