"""模块名称：Langflow服务器配置

本模块提供Langflow的服务器配置和自定义Worker实现，主要用于Gunicorn和Uvicorn集成。
主要功能包括：
- 自定义Uvicorn Worker以处理信号
- 集成Loguru的日志记录
- 配置Gunicorn应用

设计背景：为Langflow提供生产级服务器部署能力
注意事项：需要正确处理进程信号和日志记录集成
"""

import asyncio
import logging
import signal

from gunicorn import glogging
from gunicorn.app.base import BaseApplication
from lfx.log.logger import InterceptHandler
from uvicorn.workers import UvicornWorker


class LangflowUvicornWorker(UvicornWorker):
    """自定义Uvicorn Worker，用于处理进程信号"""
    
    CONFIG_KWARGS = {"loop": "asyncio"}
    _has_exited = False

    def _install_sigint_handler(self) -> None:
        """在工作进程中安装SIGQUIT处理程序
        
        决策：自定义信号处理程序
        问题：Uvicorn和Gunicorn在信号处理方面存在兼容性问题
        方案：使用asyncio事件循环添加信号处理程序
        代价：增加了信号处理的复杂性
        重评：当Uvicorn/Gunicorn修复相关问题时可移除自定义处理程序
        
        参考：
        - https://github.com/encode/uvicorn/issues/1116
        - https://github.com/benoitc/gunicorn/issues/2604
        
        关键路径（三步）：
        1) 获取运行中的事件循环
        2) 为SIGINT和SIGTERM添加处理程序
        3) 调用通用退出处理程序
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.handle_exit, signal.SIGINT, None)
        loop.add_signal_handler(signal.SIGTERM, self.handle_exit, signal.SIGTERM, None)

    def handle_exit(self, sig, frame):
        """处理退出信号
        
        关键路径（三步）：
        1) 检查是否已经退出
        2) 标记已退出状态
        3) 调用父类退出处理程序
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        if not self._has_exited:
            self._has_exited = True

        super().handle_exit(sig, frame)

    async def _serve(self) -> None:
        """启动服务
        
        关键路径（三步）：
        1) 安装信号处理程序
        2) 调用父类服务方法
        3) 处理异步服务启动
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        # 我们这样做是为了不记录"Worker (pid:XXXXX) was sent SIGINT"
        self._install_sigint_handler()
        await super()._serve()


class Logger(glogging.Logger):
    """实现并重写gunicorn日志记录接口
    
    决策：集成Loguru日志系统
    问题：Gunicorn使用标准日志系统，但项目使用Loguru
    方案：替换处理程序为InterceptHandler以将Gunicorn日志路由到Loguru
    代价：需要维护自定义日志配置
    重评：当Gunicorn提供更好的集成方式时需要重新评估
    
    此类继承自标准gunicorn日志记录器并重写它，通过用`InterceptHandler`
    替换处理程序来将gunicorn日志路由到loguru。
    
    关键路径（三步）：
    1) 初始化基类配置
    2) 设置Gunicorn日志级别
    3) 替换处理程序为InterceptHandler
    
    异常流：SIGSEGV消息被过滤并记录为调试级别
    性能瓶颈：无显著性能瓶颈
    排障入口：日志消息处理
    """

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        logging.getLogger("gunicorn.error").setLevel(logging.WARNING)
        logging.getLogger("gunicorn.access").setLevel(logging.WARNING)

        logging.getLogger("gunicorn.error").handlers = [InterceptHandler()]
        logging.getLogger("gunicorn.access").handlers = [InterceptHandler()]

    def error(self, msg, *args, **kwargs):
        """重写错误方法以过滤SIGSEGV消息
        
        关键路径（三步）：
        1) 检查消息是否包含SIGSEGV
        2) 如果包含则记录为调试级别
        3) 否则按常规方式记录错误
        
        异常流：SIGSEGV消息被降级为调试级别日志
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        # 过滤"Worker was sent SIGSEGV"消息，这些消息在macOS上很常见
        # 与多进程问题相关 - 这些通常由工作进程重启处理
        if "SIGSEGV" in str(msg):
            # 以调试级别而不是错误级别记录
            self.log.debug(msg, *args, **kwargs)
        else:
            super().error(msg, *args, **kwargs)


class LangflowApplication(BaseApplication):
    """Langflow的Gunicorn应用配置
    
    关键路径（三步）：
    1) 设置自定义Worker类和日志记录器
    2) 加载配置选项
    3) 返回应用实例
    
    异常流：无特殊异常处理
    性能瓶颈：无显著性能瓶颈
    排障入口：无特定日志关键字
    """
    
    def __init__(self, app, options=None) -> None:
        self.options = options or {}

        self.options["worker_class"] = "langflow.server.LangflowUvicornWorker"
        self.options["logger_class"] = Logger
        self.application = app
        super().__init__()

    def load_config(self) -> None:
        """加载配置选项
        
        关键路径（三步）：
        1) 筛选出有效的配置选项
        2) 将选项转换为小写键
        3) 设置到Gunicorn配置中
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        config = {key: value for key, value in self.options.items() if key in self.cfg.settings and value is not None}
        for key, value in config.items():
            self.cfg.set(key.lower(), value)

    def load(self):
        """加载应用实例
        
        关键路径（三步）：
        1) 返回存储的应用实例
        2) 无额外处理
        3) 完成加载
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        return self.application