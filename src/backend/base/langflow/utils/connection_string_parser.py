"""
模块名称：connection_string_parser

本模块提供连接字符串处理功能，主要用于数据库连接字符串的安全编码。
主要功能包括：
- 对连接字符串中的密码部分进行URL编码
- 防止特殊字符在连接字符串中引起解析错误

设计背景：数据库密码可能包含特殊字符，需要进行适当的编码以确保连接字符串的有效性
注意事项：使用rsplit方法确保从右侧开始分割，避免用户名中包含冒号的情况
"""

from urllib.parse import quote


def transform_connection_string(connection_string) -> str:
    """转换连接字符串，对密码部分进行URL编码。
    
    关键路径（三步）：
    1) 从右侧分割连接字符串，分离认证部分和数据库URL
    2) 从右侧分割认证部分，分离协议/用户名和密码
    3) 对密码进行URL编码并重组连接字符串
    
    异常流：无异常处理
    性能瓶颈：字符串处理性能
    排障入口：检查返回的连接字符串是否正确编码了密码部分
    """
    auth_part, db_url_name = connection_string.rsplit("@", 1)
    protocol_user, password_string = auth_part.rsplit(":", 1)
    encoded_password = quote(password_string)
    return f"{protocol_user}:{encoded_password}@{db_url_name}"
