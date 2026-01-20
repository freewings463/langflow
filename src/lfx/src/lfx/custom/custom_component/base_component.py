"""模块名称：自定义组件基础基类

本模块提供自定义组件的基础能力，包括代码解析、入口函数构建与模板配置生成。
主要功能包括：缓存代码解析树、校验入口函数、提取组件配置模板。

关键组件：
- `BaseComponent`：自定义组件的基础基类
- `ComponentCodeNullError`：代码缺失异常
- `ComponentFunctionEntrypointNameNullError`：入口函数名缺失异常

设计背景：在运行时安全构建用户自定义组件并提供模板化配置。
注意事项：`_code` 与 `_function_entrypoint_name` 必须有效，否则会抛出 HTTP 异常。
"""

import copy
import operator
import re
from typing import TYPE_CHECKING, Any, ClassVar

from cachetools import TTLCache, cachedmethod
from fastapi import HTTPException

from lfx.custom import validate
from lfx.custom.attributes import ATTR_FUNC_MAPPING
from lfx.custom.code_parser.code_parser import CodeParser
from lfx.custom.eval import eval_custom_component_code
from lfx.log.logger import logger

if TYPE_CHECKING:
    from uuid import UUID


class ComponentCodeNullError(HTTPException):
    """组件代码为空异常。

    契约：用于提示自定义组件缺少代码；副作用无。
    决策：继承 `HTTPException` 以统一 API 错误格式
    问题：前端需要结构化错误响应
    方案：沿用 FastAPI 异常类型
    代价：与内部异常类型混用
    重评：当错误体系统一时改为自定义错误类
    """

    pass


class ComponentFunctionEntrypointNameNullError(HTTPException):
    """组件入口函数名为空异常。

    契约：用于提示入口函数名缺失；副作用无。
    决策：继承 `HTTPException` 以统一响应
    问题：入口函数名为空将导致无法执行
    方案：在构建阶段直接抛错
    代价：调用方需要处理 HTTP 异常
    重评：当入口函数可自动推断时移除此异常
    """

    pass


class BaseComponent:
    """自定义组件基础类。

    契约：输入用户代码与入口函数名；输出可执行函数或模板配置；
    副作用：缓存代码解析树；失败语义：代码/入口名为空抛 `HTTPException`。
    关键路径：1) 解析代码树 2) 创建入口函数 3) 生成模板配置。
    决策：使用 TTL 缓存解析结果
    问题：频繁解析用户代码开销大
    方案：`TTLCache` 缓存解析树
    代价：短时间内变更代码可能命中旧缓存
    重评：当需要强一致时减少 TTL 或禁用缓存
    """
    ERROR_CODE_NULL: ClassVar[str] = "Python code must be provided."
    ERROR_FUNCTION_ENTRYPOINT_NAME_NULL: ClassVar[str] = "The name of the entrypoint function must be provided."

    def __init__(self, **data) -> None:
        """初始化基础组件状态。

        契约：输入关键字参数；输出无；副作用：设置实例字段与缓存；
        失败语义：无。
        关键路径：1) 初始化默认字段 2) 应用输入参数 3) 设置不可变 user_id。
        决策：允许动态属性写入
        问题：自定义组件字段不固定
        方案：遍历 `data` 并写入实例
        代价：弱类型导致运行期错误
        重评：当字段规范化后引入显式模型
        """
        self._code: str | None = None
        self._function_entrypoint_name: str = "build"
        self.field_config: dict = {}
        self._user_id: str | UUID | None = None
        self._template_config: dict = {}

        self.cache: TTLCache = TTLCache(maxsize=1024, ttl=60)

        for key, value in data.items():
            if key == "user_id":
                self._user_id = value
            else:
                setattr(self, key, value)

    def __setattr__(self, key, value) -> None:
        """拦截属性赋值以保护 `user_id` 不可变。

        契约：输入键值对；输出无；副作用：可能记录警告；
        失败语义：无。
        关键路径：1) 检测 `_user_id` 变更 2) 记录警告 3) 继续赋值。
        决策：对 `_user_id` 变更仅告警不阻断
        问题：需要在运行期阻止用户意外修改
        方案：输出日志提示
        代价：仍允许覆盖实际值
        重评：当需要强制不可变时改为抛异常
        """
        if key == "_user_id":
            try:
                if self._user_id is not None:
                    logger.warning("user_id is immutable and cannot be changed.")
            except (KeyError, AttributeError):
                pass
        super().__setattr__(key, value)

    @property
    def code(self) -> str | None:
        """获取组件代码。

        契约：输入无；输出代码字符串或 None；副作用无；失败语义：无。
        关键路径：1) 返回 `_code`。
        决策：不做空值补齐
        问题：保持调用方对缺失代码的判断权
        方案：原样返回
        代价：调用方需自行校验
        重评：当需要默认模板时返回默认代码
        """
        return self._code

    @property
    def function_entrypoint_name(self) -> str:
        """获取入口函数名。

        契约：输入无；输出入口函数名；副作用无；失败语义：无。
        关键路径：1) 返回 `_function_entrypoint_name`。
        决策：默认入口名为 `build`
        问题：与模板生成约定保持一致
        方案：初始化时写死默认值
        代价：自定义入口需显式覆盖
        重评：当支持自动发现入口时移除默认
        """
        return self._function_entrypoint_name

    @cachedmethod(cache=operator.attrgetter("cache"))
    def get_code_tree(self, code: str):
        """解析并缓存代码树。

        契约：输入代码字符串；输出解析树；副作用：缓存解析结果；
        失败语义：解析失败会抛异常。
        关键路径：1) 构造 `CodeParser` 2) 解析代码。
        决策：使用缓存装饰器
        问题：重复解析开销大
        方案：按代码字符串缓存解析树
        代价：不同但等价代码仍会多次解析
        重评：当需要更强缓存命中时引入哈希归一化
        """
        parser = CodeParser(code)
        return parser.parse_code()

    def get_function(self):
        """构建入口函数。

        契约：输入无；输出可调用函数；副作用：无；
        失败语义：代码或入口名为空抛 HTTP 异常。
        关键路径：1) 校验 `_code` 2) 校验入口名 3) 创建函数。
        决策：优先使用 `validate.create_function`
        问题：需要沙箱化创建用户代码函数
        方案：委托 `validate` 模块
        代价：依赖外部校验实现
        重评：当引入更严格沙箱时替换实现
        """
        if not self._code:
            raise ComponentCodeNullError(
                status_code=400,
                detail={"error": self.ERROR_CODE_NULL, "traceback": ""},
            )

        if not self._function_entrypoint_name:
            raise ComponentFunctionEntrypointNameNullError(
                status_code=400,
                detail={
                    "error": self.ERROR_FUNCTION_ENTRYPOINT_NAME_NULL,
                    "traceback": "",
                },
            )

        return validate.create_function(self._code, self._function_entrypoint_name)

    @staticmethod
    def get_template_config(component):
        """生成自定义组件模板配置。

        契约：输入组件实例；输出模板配置字典；副作用无；
        失败语义：无。
        关键路径：1) 遍历 `ATTR_FUNC_MAPPING` 2) 深拷贝值并应用转换 3) 清理无效键。
        决策：使用深拷贝防止副作用
        问题：避免原组件配置被修改
        方案：对属性值做 `deepcopy`
        代价：深拷贝开销较大
        重评：当属性值可视为不可变时移除深拷贝
        """
        template_config = {}

        for attribute, func in ATTR_FUNC_MAPPING.items():
            if hasattr(component, attribute):
                value = getattr(component, attribute)
                if value is not None:
                    value_copy = copy.deepcopy(value)
                    template_config[attribute] = func(value=value_copy)

        for key in template_config.copy():
            if key not in ATTR_FUNC_MAPPING:
                template_config.pop(key, None)

        return template_config

    def build_template_config(self) -> dict:
        """构建模板配置字典。

        契约：输入无；输出模板配置；副作用：解析并执行组件代码；
        失败语义：代码解析失败可能抛 `ImportError` 或其他异常。
        关键路径：1) 校验代码 2) 执行自定义组件代码 3) 提取模板配置。
        决策：对属性不存在错误转为 `ImportError`
        问题：用户代码中模块属性缺失需提示
        方案：匹配异常信息并包装
        代价：误判导致异常类型变化
        重评：当异常结构稳定时改为精准判断
        """
        if not self._code:
            return {}

        try:
            cc_class = eval_custom_component_code(self._code)

        except AttributeError as e:
            pattern = r"module '.*?' has no attribute '.*?'"
            if re.search(pattern, str(e)):
                raise ImportError(e) from e
            raise

        component_instance = cc_class(_code=self._code)
        return self.get_template_config(component_instance)

    def build(self, *args: Any, **kwargs: Any) -> Any:
        """抽象构建方法，由子类实现。

        契约：输入任意参数；输出任意；副作用未知；
        失败语义：未实现时抛 `NotImplementedError`。
        关键路径：由子类定义。
        决策：保持抽象接口
        问题：强制子类提供实现
        方案：抛出 `NotImplementedError`
        代价：需要子类实现具体逻辑
        重评：当提供默认构建逻辑时移除此抽象
        """
        raise NotImplementedError
