"""
模块名称：settings.feature_flags

本模块提供轻量级特性开关配置，用于在不改代码的情况下控制实验功能启用。
主要功能包括：
- 从环境变量读取特性开关
- 提供默认值以保障启动稳定性

关键组件：
- FeatureFlags：特性开关模型
- FEATURE_FLAGS：全局单例实例

设计背景：特性开关需要在部署层可控，便于灰度/回滚。
注意事项：所有开关均以 `LANGFLOW_FEATURE_` 前缀读取环境变量。
"""

from pydantic_settings import BaseSettings


class FeatureFlags(BaseSettings):
    """特性开关集合。

    契约：
    - 输入：环境变量 `LANGFLOW_FEATURE_*`
    - 输出：布尔型开关值
    - 副作用：无
    - 失败语义：解析失败时使用字段默认值
    """

    mvp_components: bool = False

    class Config:
        env_prefix = "LANGFLOW_FEATURE_"


FEATURE_FLAGS = FeatureFlags()
