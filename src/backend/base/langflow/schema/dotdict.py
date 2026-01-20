"""
模块名称：点号访问字典

本模块提供 `dotdict`，允许通过属性访问字典键，主要用于简化配置与结构化数据读取。主要功能包括：
- 将嵌套字典自动转换为 `dotdict`
- 支持点号读写与删除

关键组件：
- dotdict

设计背景：提升可读性，降低多层字典访问的样板代码。
注意事项：键名与 `dict` 方法冲突时需使用 `dict['key']` 访问。
"""


class dotdict(dict):  # noqa: N801
    """支持点号访问的字典。

    契约：仅对可作为属性名的键提供点号访问。
    副作用：访问嵌套字典时会就地替换为 `dotdict`，影响后续读取。
    失败语义：访问不存在键抛 `AttributeError`。
    """

    Note:
        - Only keys that are valid attribute names (e.g., strings that could be variable names) are accessible via dot
          notation.
        - Keys which are not valid Python attribute names or collide with the dict method names (like 'items', 'keys')
          should be accessed using the traditional dict['key'] notation.
    """

    def __getattr__(self, attr):
        """点号读取并自动嵌套转换。

        契约：返回键对应的值，若为 `dict` 则转为 `dotdict` 并写回。
        失败语义：不存在时抛 `AttributeError`，与 `getattr` 行为一致。
        """
        try:
            value = self[attr]
            if isinstance(value, dict) and not isinstance(value, dotdict):
                value = dotdict(value)
                self[attr] = value
        except KeyError as e:
            msg = f"'dotdict' object has no attribute '{attr}'"
            raise AttributeError(msg) from e
        else:
            return value

    def __setattr__(self, key, value) -> None:
        """点号写入，必要时嵌套转换。"""
        if isinstance(value, dict) and not isinstance(value, dotdict):
            value = dotdict(value)
        self[key] = value

    def __delattr__(self, key) -> None:
        """点号删除对应键。

        失败语义：不存在时抛 `AttributeError`。
        """
        try:
            del self[key]
        except KeyError as e:
            msg = f"'dotdict' object has no attribute '{key}'"
            raise AttributeError(msg) from e

    def __missing__(self, key):
        """缺失键时返回空 `dotdict`，便于链式访问。"""
        return dotdict()
