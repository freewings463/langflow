"""
模块名称：`DataFrame` 兼容导出

本模块转发 `lfx.schema.dataframe` 的 `DataFrame`，主要用于旧路径兼容。主要功能包括：
- 保留 `langflow.schema.dataframe.DataFrame` 的导入路径

关键组件：
- DataFrame

设计背景：历史代码仍依赖 `langflow.schema.dataframe`。
注意事项：仅导出类型别名，不新增逻辑。
"""

from lfx.schema.dataframe import DataFrame  # noqa: F401
