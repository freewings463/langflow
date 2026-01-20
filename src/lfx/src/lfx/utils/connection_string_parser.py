"""模块名称：连接串解析与编码

模块目的：确保数据库连接串中的密码段可安全传输。
主要功能：
- 对 `user:password@host` 中的 `password` 进行 URL 编码
使用场景：构建 SQLAlchemy/驱动连接串时包含特殊字符密码。
关键组件：`transform_connection_string`
设计背景：密码包含特殊字符会破坏连接串格式，需要统一编码。
注意事项：输入必须包含可拆分的 `@` 与 `:`，否则会抛出 `ValueError`。
"""

from urllib.parse import quote


def transform_connection_string(connection_string) -> str:
    """对连接串中的密码段做 URL 编码。

    契约：期望结构包含最后一个 `@` 与最后一个 `:` 作为分隔符。
    失败语义：分隔符缺失会抛出 `ValueError`（来自 `rsplit`）。
    """
    auth_part, db_url_name = connection_string.rsplit("@", 1)
    protocol_user, password_string = auth_part.rsplit(":", 1)
    encoded_password = quote(password_string)
    return f"{protocol_user}:{encoded_password}@{db_url_name}"
