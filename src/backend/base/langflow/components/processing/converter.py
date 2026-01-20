# 转发导入：提供处理转换工具的兼容入口
# 我们刻意保留此文件，因为 components/__init__.py 中到 lfx 的重定向
# 只支持从 lfx.components 的直接导入，不支持子模块。
#
# 这样可确保仍能通过 langflow.components.processing.converter 进行导入。
from lfx.components.processing.converter import convert_to_dataframe

__all__ = ["convert_to_dataframe"]
