"""
模块名称：`Store` 服务异常定义

本模块定义 `Store` 服务层的自定义异常与默认状态码映射，主要用于统一错误语义。
主要功能包括：
- 提供 `CustomError` 及常见鉴权/过滤错误子类。

关键组件：`CustomError`、`UnauthorizedError`、`ForbiddenError`、`APIKeyError`、`FilterError`。
设计背景：服务层需要在不依赖具体框架的情况下表达 `HTTP` 状态。
使用场景：`Store` 服务调用中断、权限不足、参数过滤错误。
注意事项：状态码映射应与 `API` 层规范保持一致。
"""


class CustomError(Exception):
    """`Store` 服务自定义异常基类。

    契约：输入 `detail/status_code`，输出异常实例；副作用：无。
    关键路径：保存 `status_code` 以供上层转译为响应。
    决策：在异常对象中携带状态码
    问题：服务层需传递 `HTTP` 语义但不直接依赖框架
    方案：在基类中存储 `status_code`
    代价：异常类与 `HTTP` 语义耦合
    重评：若改为统一错误码体系
    """

    def __init__(self, detail: str, status_code: int):
        super().__init__(detail)
        self.status_code = status_code


# 注意：以下异常类预置常见 `HTTP` 状态码，供上层直接使用。
class UnauthorizedError(CustomError):
    """未授权访问异常。

    契约：无输入或自定义 `detail`，输出异常实例；副作用：无。
    关键路径：默认状态码为 401。
    决策：使用 401 表示未认证
    问题：需要区分认证失败与权限不足
    方案：固定 401 作为默认状态码
    代价：无法细分多种认证错误原因
    重评：若引入细粒度错误码
    """

    def __init__(self, detail: str = "Unauthorized access"):
        super().__init__(detail, 401)


class ForbiddenError(CustomError):
    """无权限访问异常。

    契约：无输入或自定义 `detail`，输出异常实例；副作用：无。
    关键路径：默认状态码为 403。
    决策：使用 403 表示鉴权通过但无权限
    问题：需要明确区分权限不足
    方案：固定 403 作为默认状态码
    代价：无法表达细化的权限策略
    重评：若加入基于角色的错误细分
    """

    def __init__(self, detail: str = "Forbidden"):
        super().__init__(detail, 403)


class APIKeyError(CustomError):
    """`API key` 相关错误。

    契约：无输入或自定义 `detail`，输出异常实例；副作用：无。
    关键路径：默认状态码为 400（当前实现）。
    决策：沿用历史状态码以保持兼容
    问题：历史客户端依赖 400 进行错误分支
    方案：暂保留 400，后续评估迁移
    代价：与语义上更合理的 401 不一致
    重评：当客户端适配后切换为 401
    """

    def __init__(self, detail: str = "API key error"):
        super().__init__(detail, 400)


class FilterError(CustomError):
    """过滤条件错误。

    契约：无输入或自定义 `detail`，输出异常实例；副作用：无。
    关键路径：默认状态码为 400。
    决策：以 400 表示请求过滤参数错误
    问题：过滤条件非法需快速反馈给调用方
    方案：使用 400 作为默认状态码
    代价：无法区分不同过滤器的错误类型
    重评：若需要更细粒度的参数错误分类
    """

    def __init__(self, detail: str = "Filter error"):
        super().__init__(detail, 400)
