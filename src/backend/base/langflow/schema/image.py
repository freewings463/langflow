"""
模块名称：`image` 兼容导出

本模块转发 `lfx.schema.image` 的图像相关模型，主要用于旧路径兼容。主要功能包括：
- 保留 `langflow.schema.image` 的导入路径

关键组件：
- image 模块中的导出符号（按 `lfx` 定义）

设计背景：历史代码仍依赖 `langflow.schema.image`。
注意事项：此处使用通配导入，导出范围由上游模块控制。
"""

from lfx.schema.image import *  # noqa: F403
