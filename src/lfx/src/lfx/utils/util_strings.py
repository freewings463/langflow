"""模块名称：字符串与连接串校验工具

模块目的：控制输出字符串长度并校验数据库连接 URL。
主要功能：
- 递归截断过长字符串
- 校验 SQLAlchemy 兼容的数据库 URL
使用场景：日志保护、缓存入库、配置校验。
关键组件：`truncate_long_strings`、`is_valid_database_url`
设计背景：输出截断用于日志/存储保护，URL 校验用于早期失败提示。
注意事项：截断函数会原地修改传入的字典/列表结构。
"""

from lfx.serialization import constants


def truncate_long_strings(data, max_length=None):
    """递归截断过长字符串（原地修改）。

    关键路径：
    1) 解析 `max_length` 默认值
    2) 递归遍历 dict/list
    3) 超长字符串截断并追加省略号

    契约：`max_length=None` 时使用 `constants.MAX_TEXT_LENGTH`；
    `max_length<0` 直接返回原数据。
    """
    if max_length is None:
        max_length = constants.MAX_TEXT_LENGTH

    if max_length < 0:
        return data

    if not isinstance(data, dict | list):
        if isinstance(data, str) and len(data) > max_length:
            return data[:max_length] + "..."
        return data

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and len(value) > max_length:
                data[key] = value[:max_length] + "..."
            elif isinstance(value, (dict | list)):
                truncate_long_strings(value, max_length)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, str) and len(item) > max_length:
                data[index] = item[:max_length] + "..."
            elif isinstance(item, (dict | list)):
                truncate_long_strings(item, max_length)

    return data


def is_valid_database_url(url: str) -> bool:
    """校验 SQLAlchemy 兼容的数据库连接 URL。"""
    try:
        from sqlalchemy.engine import make_url

        parsed_url = make_url(url)
        parsed_url.get_dialect()
        parsed_url.get_driver_name()

    except Exception:  # noqa: BLE001
        return False

    return True
