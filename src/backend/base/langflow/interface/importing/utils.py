"""
模块名称：动态导入工具

本模块提供按路径导入模块/类的工具函数，主要用于运行时加载 `LangChain`/自定义组件。主要功能包括：
- 支持 `from x import y` 形式的解析导入
- 统一屏蔽已知 `pydantic` 配置告警

关键组件：
- `import_module`：模块或对象导入
- `import_class`：类对象导入

设计背景：组件定义常以字符串存储，需在运行时解析
注意事项：导入失败会抛出 `ImportError`/`AttributeError`
"""

import importlib
from typing import Any


def import_module(module_path: str) -> Any:
    """按路径导入模块或对象。

    契约：输入模块路径字符串；返回模块对象或其属性对象。
    关键路径：1) 判断是否为 `from ... import ...` 语法 2) 导入模块 3) 可选返回属性。
    失败语义：模块不存在抛 `ImportError`；属性不存在抛 `AttributeError`。
    决策：抑制 `pydantic` 已知弃用告警
    问题：第三方依赖版本波动导致噪声日志
    方案：用 `warnings.catch_warnings` 屏蔽特定告警
    代价：可能掩盖真实配置问题
    重评：当告警被修复或需要暴露给用户时
    """
    if "from" not in module_path:
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
            )
            warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
            return importlib.import_module(module_path)
    _, module_path, _, object_name = module_path.split()

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

    契约：输入 `module.ClassName`；返回类对象。
    关键路径：1) 拆分模块/类名 2) 调用 `import_module` 3) 取属性。
    失败语义：模块或类不存在抛 `ImportError`/`AttributeError`。
    """
    module_path, class_name = class_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, class_name)
