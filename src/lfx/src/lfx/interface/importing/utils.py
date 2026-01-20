"""
模块名称：动态导入工具

本模块提供按字符串路径导入模块与类的工具函数。
主要功能包括：
- 解析模块路径并安全导入
- 兼容 `from x import y` 形式
- 屏蔽部分弃用警告避免噪声

关键组件：
- `import_module`：按模块路径导入对象
- `import_class`：按类路径导入类对象

设计背景：统一动态导入逻辑，减少重复代码。
使用场景：运行时根据配置字符串加载实现类。
注意事项：导入失败会抛出异常，调用方需处理。
"""

import importlib
from typing import Any


def import_module(module_path: str) -> Any:
    """按路径导入模块或对象。

    契约：支持纯模块路径与 `from a.b import C` 语法。
    副作用：触发模块导入并临时调整警告过滤。
    失败语义：导入失败抛 `ImportError` 或 `AttributeError`。
    决策：在导入过程中屏蔽已知弃用警告。
    问题：部分依赖在导入时产生大量非关键信息警告。
    方案：在 `warnings.catch_warnings()` 中过滤指定警告。
    代价：可能掩盖真实兼容性问题。
    重评：当依赖升级消除警告后移除过滤。
    """
    if "from" not in module_path:
        # 注意：直接按模块路径导入。
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
            )
            warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
            return importlib.import_module(module_path)
    # 实现：解析 `from x import y` 形式。
    _, module_path, _, object_name = module_path.split()

    # 实现：导入模块后返回对象。
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
        )
        warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
        module = importlib.import_module(module_path)

    return getattr(module, object_name)


def import_class(class_path: str) -> Any:
    """按类路径导入类对象。

    契约：`class_path` 形如 `package.module.ClassName`。
    副作用：触发模块导入。
    失败语义：类不存在将抛 `AttributeError`。
    决策：复用 `import_module` 统一导入与警告处理。
    问题：多处重复导入逻辑容易导致行为不一致。
    方案：拆分模块路径后调用 `import_module`。
    代价：增加一次字符串拆分。
    重评：当需要针对类导入的特定行为时拆分实现。
    """
    module_path, class_name = class_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, class_name)
