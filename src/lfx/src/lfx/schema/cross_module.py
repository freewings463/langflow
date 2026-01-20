"""模块名称：跨模块模型兼容层

本模块提供跨模块 `isinstance` 兼容的元类与基模型，解决同名模型在不同导出路径下互认失败的问题。主要功能包括：
- 结构化实例检查：基于类名与字段集合，而非模块路径
- 兼容重导出：支持 `lfx` 与 `langflow` 等不同入口的同名模型
- 最小侵入：不改变 Pydantic 的校验与序列化行为

关键组件：
- CrossModuleMeta：覆盖 `__instancecheck__` 的元类
- CrossModuleModel：统一基类，供可重导出模型继承

设计背景：模块重导出导致 `isinstance` 因模块路径不同而失败，需要结构化判断以保持兼容。
注意事项：仅比较类名与字段集合，同名异构模型可能误判。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CrossModuleMeta(type(BaseModel)):  # type: ignore[misc]
    """为 Pydantic 模型提供跨模块 `isinstance` 兼容判断。

    契约：输入任意实例，输出 bool；副作用无。
    失败语义：不抛异常，无法判定时返回 False。
    决策：结构化判断而非模块路径。
    问题：重导出导致类路径不同，`isinstance` 误判。
    方案：比对类名与字段集合（含 `model_fields`）。
    代价：同名异构模型可能被误判为兼容。
    重评：当出现同名但字段差异显著的模型族时。
    """

    def __instancecheck__(cls, instance: Any) -> bool:
        """执行跨模块实例兼容性检查。

        契约：输入实例 -> bool；副作用无。
        关键路径（三步）：
        1) 标准 `isinstance` 检查
        2) 判断 `model_fields` 与类名
        3) 校验字段集合兼容性
        失败语义：字段缺失或类名不匹配时返回 False。
        性能瓶颈：字段集合构建与比较（O(n)）。
        排障入口：无日志；调用方可记录失败路径。
        """
        if type.__instancecheck__(cls, instance):
            return True

        # 注意：跨模块兼容仅依赖类名 + 字段集合，同名异构模型可能误判。
        if not hasattr(instance, "model_fields"):
            return False

        if instance.__class__.__name__ != cls.__name__:
            return False

        cls_fields = set(cls.model_fields.keys()) if hasattr(cls, "model_fields") else set()
        instance_fields = set(instance.model_fields.keys())

        # 实现：实例字段必须覆盖类字段，允许额外字段。
        return cls_fields.issubset(instance_fields)


class CrossModuleModel(BaseModel, metaclass=CrossModuleMeta):
    """可跨模块互认的 Pydantic 基类。

    契约：继承该类不会改变模型字段或序列化行为；副作用无。
    关键路径：通过 CrossModuleMeta 的 `__instancecheck__` 实现兼容判断。
    决策：集中在基类以避免每个模型重复实现。
    问题：多个导出路径导致 `isinstance` 不可靠。
    方案：统一基类 + 元类结构化判断。
    代价：继承树需统一基类，第三方模型需手动适配。
    重评：当 Pydantic 提供官方跨模块兼容支持时。
    """
