"""
模块名称：`CrewAI` 任务占位类型

本模块提供 `CrewAI` 任务类型的占位实现，主要用于在可选依赖缺失时保持类型判断与路由能力。
主要功能包括：
- 顺序任务占位类型 `SequentialTask`
- 层级任务占位类型 `HierarchicalTask`

关键组件：
- `SequentialTask`、`HierarchicalTask`

设计背景：`CrewAI` 作为可选依赖时需要避免导入失败阻断模块加载。
注意事项：当 `crewai` 未安装时，`Task` 退化为 `object`，仅保留类型存在性。
"""

# 决策：在缺失 `crewai` 依赖时使用空壳 `Task`
# 问题：可选依赖缺失会阻断模块导入
# 方案：`ImportError` 时回退为 `object`
# 代价：失去 `Task` 的类型/方法约束
# 重评：当 `crewai` 变为强依赖或提供稳定 stub 时
try:
    from crewai import Task
except ImportError:
    Task = object


class SequentialTask(Task):
    """CrewAI 顺序任务占位类型。

    契约：继承 `Task`；无额外行为；副作用：无。
    失败语义：依赖缺失时仅具备空壳类型行为。
    关键路径：1) 上层选择顺序执行；2) CrewAI 逐个调度；3) 输出遵循 `Task` 约定。
    决策：保留空类以匹配顺序任务的类型判断
    问题：调用方需要显式区分顺序与层级任务
    方案：提供独立子类供 `isinstance` 判断
    代价：类型本身不承载配置或行为
    重评：当需要在任务类中携带顺序策略时
    """
    pass


class HierarchicalTask(Task):
    """CrewAI 层级任务占位类型。

    契约：继承 `Task`；无额外行为；副作用：无。
    失败语义：依赖缺失时仅具备空壳类型行为。
    关键路径：1) 上层选择层级调度；2) manager/agent 组合执行；3) 输出遵循 `Task` 约定。
    决策：使用独立子类承载层级调度语义
    问题：层级任务需要与顺序任务区分调度策略
    方案：保留空子类用于路由与匹配
    代价：未在类型上表达 manager 配置
    重评：当层级调度需要显式字段或配置时
    """
    pass
