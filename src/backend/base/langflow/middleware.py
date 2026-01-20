"""模块名称：内容大小限制中间件

本模块提供限制上传文件大小的功能，主要用于防止过大文件上传导致的服务资源耗尽。
主要功能包括：
- 检查上传内容的大小
- 在超出限制时抛出异常
- 提供自定义异常类

设计背景：保护服务免受过大文件上传的影响
注意事项：需要正确配置最大文件大小限制并在超出时适当处理异常
"""

from fastapi import HTTPException
from lfx.log.logger import logger

from langflow.services.deps import get_settings_service


class MaxFileSizeException(HTTPException):
    """超过最大文件大小限制时抛出的异常"""
    
    def __init__(self, detail: str = "File size is larger than the maximum file size {}MB"):
        super().__init__(status_code=413, detail=detail)


# 从 https://github.com/steinnes/content-size-limit-asgi/blob/master/content_size_limit_asgi/middleware.py#L26 修改而来
class ContentSizeLimitMiddleware:
    """ASGI应用的内容大小限制中间件
    
    决策：使用中间件限制内容大小
    问题：需要防止过大文件上传消耗过多服务资源
    方案：在接收请求体时实时检查内容大小
    代价：增加了请求处理的复杂性和少量性能开销
    重评：当需要更精细的流量控制时需要重新评估
    
    参数说明：
      app (ASGI application): ASGI应用
      max_content_size (optional): 允许的最大内容大小（以字节为单位），None表示无限制
      exception_cls (optional): 要引发的异常类（默认为ContentSizeExceeded）
    
    关键路径（三步）：
    1) 包装receive函数以监控内容大小
    2) 在接收数据时检查大小限制
    3) 超限时抛出异常，否则继续处理
    
    异常流：超过大小限制时抛出MaxFileSizeException
    性能瓶颈：实时大小检查可能略微影响性能
    排障入口：大小限制相关的错误日志
    """

    def __init__(
        self,
        app,
    ):
        self.app = app
        self.logger = logger

    @staticmethod
    def receive_wrapper(receive):
        """创建一个包装器来监控接收的内容大小
        
        关键路径（三步）：
        1) 获取最大文件上传大小配置
        2) 累计接收的数据大小
        3) 检查是否超出限制并相应处理
        
        异常流：超出限制时抛出MaxFileSizeException
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        received = 0

        async def inner():
            max_file_size_upload = get_settings_service().settings.max_file_size_upload
            nonlocal received
            message = await receive()
            if message["type"] != "http.request" or max_file_size_upload is None:
                return message
            body_len = len(message.get("body", b""))
            received += body_len
            if received > max_file_size_upload * 1024 * 1024:
                # max_content_size以字节为单位，转换为MB
                received_in_mb = round(received / (1024 * 1024), 3)
                msg = (
                    f"Content size limit exceeded. Maximum allowed is {max_file_size_upload}MB"
                    f" and got {received_in_mb}MB."
                )
                raise MaxFileSizeException(msg)
            return message

        return inner

    async def __call__(self, scope, receive, send):
        """中间件调用入口
        
        关键路径（三步）：
        1) 检查请求类型是否为HTTP
        2) 如果是HTTP请求则使用包装器处理
        3) 否则直接调用下游应用
        
        异常流：超出大小限制时抛出异常
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        wrapper = self.receive_wrapper(receive)
        await self.app(scope, wrapper, send)