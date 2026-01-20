"""
模块名称：服务基类定义

本模块定义了所有服务的抽象基类，提供通用的服务功能接口。
主要功能包括：
- 定义服务的通用属性和方法
- 提供服务方法的反射机制
- 提供服务状态管理

关键组件：
- `Service`：服务抽象基类

设计背景：为所有Langflow服务提供统一的接口和功能。
注意事项：所有具体服务实现都应继承此类。
"""

from abc import ABC


class Service(ABC):
    name: str
    ready: bool = False

    def get_schema(self):
        """构建一个字典，列出所有方法、参数、类型、返回类型和文档。

        契约：返回包含服务方法信息的字典。
        副作用：无。
        失败语义：不抛出异常。
        """
        schema = {}
        ignore = ["teardown", "set_ready"]
        for method in dir(self):
            if method.startswith("_") or method in ignore:
                continue
            func = getattr(self, method)
            schema[method] = {
                "name": method,
                "parameters": func.__annotations__,
                "return": func.__annotations__.get("return"),
                "documentation": func.__doc__,
            }
        return schema

    async def teardown(self) -> None:
        """服务停用时的清理方法。

        契约：执行服务停用前的清理操作。
        副作用：无。
        失败语义：不抛出异常。
        """
        return

    def set_ready(self) -> None:
        """设置服务为就绪状态。

        契约：将服务的就绪状态设为 True。
        副作用：修改 ready 属性。
        失败语义：不抛出异常。
        """
        self.ready = True