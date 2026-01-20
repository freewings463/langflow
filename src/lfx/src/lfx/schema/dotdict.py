"""dotdict 实现。

本模块提供点号访问的字典实现，便于以属性方式读写键值。
"""


class dotdict(dict):  # noqa: N801
    """支持点号访问的字典类型。

    关键路径（三步）：
    1) 点号访问转为字典取值；
    2) 点号赋值转为字典写入；
    3) 递归将嵌套 dict 转为 dotdict。
    """

    def __getattr__(self, attr):
        """点号访问转为字典查找并自动转换嵌套 dict。"""
        try:
            value = self[attr]
            if isinstance(value, dict) and not isinstance(value, dotdict):
                value = dotdict(value)
                self[attr] = value  # 更新为嵌套 dotdict 以便后续访问
        except KeyError as e:
            msg = f"'dotdict' object has no attribute '{attr}'"
            raise AttributeError(msg) from e
        else:
            return value

    def __setattr__(self, key, value) -> None:
        """点号赋值转为字典写入。"""
        if isinstance(value, dict) and not isinstance(value, dotdict):
            value = dotdict(value)
        self[key] = value

    def __delattr__(self, key) -> None:
        """点号删除转为字典删除。"""
        try:
            del self[key]
        except KeyError as e:
            msg = f"'dotdict' object has no attribute '{key}'"
            raise AttributeError(msg) from e

    def __missing__(self, key):
        """缺失键返回空 dotdict，便于链式访问。"""
        return dotdict()
