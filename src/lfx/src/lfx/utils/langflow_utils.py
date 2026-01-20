"""模块名称：Langflow 运行环境探测

模块目的：提供 `langflow` 包可用性的懒加载检测。
主要功能：检测是否安装 `langflow` 并缓存结果。
使用场景：可选依赖场景下的条件启用与能力降级。
关键组件：`_LangflowModule`、`has_langflow_memory`
设计背景：避免启动时强依赖可选包，并减少重复 `find_spec` 开销。
注意事项：缓存为进程级静态状态，运行期安装/卸载不会自动刷新。
"""

import importlib.util

from lfx.log.logger import logger


class _LangflowModule:
    # 注意：三态缓存
    # - None：尚未检查
    # - True：已确认可用
    # - False：已确认不可用
    _available = None

    @classmethod
    def is_available(cls):
        return cls._available

    @classmethod
    def set_available(cls, value):
        cls._available = value


def has_langflow_memory():
    """检查 `langflow` 包是否可用并缓存结果。

    失败语义：导入探测异常将记录日志并视为不可用。
    """
    # TODO：后续可改为更细粒度的服务发现与运行期变更感知。

    # 使用缓存结果，避免重复探测

    is_langflow_available = _LangflowModule.is_available()

    if is_langflow_available is not None:
        return is_langflow_available

    # 首次探测并缓存

    module_spec = None

    try:
        module_spec = importlib.util.find_spec("langflow")
    except ImportError:
        pass
    except (TypeError, ValueError) as e:
        logger.error(f"Error encountered checking for langflow.memory: {e}")

    is_langflow_available = module_spec is not None
    _LangflowModule.set_available(is_langflow_available)

    return is_langflow_available
