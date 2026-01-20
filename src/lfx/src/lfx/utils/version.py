"""模块名称：版本信息工具

模块目的：提供版本信息与预发布判断能力。
主要功能：
- 返回基础版本信息
- 判断版本号是否为预发布
使用场景：兼容性检查、展示版本信息、发布标识判断。
关键组件：`get_version_info`、`is_pre_release`
设计背景：对外接口需要统一版本结构。
注意事项：`get_version_info` 目前为占位实现，需与实际发布流程保持一致。
"""


def get_version_info():
    """获取版本信息（当前为占位实现）。"""
    return {"version": "0.1.0", "package": "lfx"}


def is_pre_release(version: str) -> bool:
    """判断版本号是否为预发布。"""
    # 常见预发布标记
    pre_release_indicators = ["alpha", "beta", "rc", "dev", "a", "b"]
    version_lower = version.lower()
    return any(indicator in version_lower for indicator in pre_release_indicators)
