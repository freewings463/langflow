"""
模块名称：version

本模块提供版本信息处理功能，主要用于获取和比较软件包版本。
主要功能包括：
- 计算非预发布版本号
- 获取包版本信息
- 检查版本是否为预发布版本
- 检查版本是否为夜间版本
- 获取最新版本信息

设计背景：在应用程序中需要准确识别和处理不同类型的版本号，包括预发布版本和夜间版本
注意事项：使用packaging库进行版本比较，使用importlib.metadata获取包信息
"""

from importlib import metadata

import httpx
from packaging import version as pkg_version


def _compute_non_prerelease_version(prerelease_version: str) -> str:
    """计算非预发布版本号。
    
    关键路径（三步）：
    1) 遍历预发布关键词列表
    2) 如果版本号包含关键词，则分割并返回基础版本
    3) 否则返回原版本号
    
    异常流：无异常处理
    性能瓶颈：字符串操作
    排障入口：检查返回的版本号是否正确移除了预发布标识
    """
    prerelease_keywords = ["a", "b", "rc", "dev", "post"]
    for keyword in prerelease_keywords:
        if keyword in prerelease_version:
            return prerelease_version.split(keyword)[0][:-1]
    return prerelease_version


def _get_version_info():
    """从可能的包名称列表中检索包的版本。
    
    关键路径（三步）：
    1) 遍历可能的包名称选项
    2) 尝试获取每个包的版本信息
    3) 计算非预发布版本并返回版本信息
    
    异常流：如果在所有选项中都找不到包，则抛出ValueError
    性能瓶颈：包元数据的检索
    排障入口：检查是否可以从已安装的包中获取版本信息
    """
    package_options = [
        ("langflow", "Langflow"),
        ("langflow-base", "Langflow Base"),
        ("langflow-nightly", "Langflow Nightly"),
        ("langflow-base-nightly", "Langflow Base Nightly"),
    ]
    __version__ = None
    for pkg_name, display_name in package_options:
        try:
            __version__ = metadata.version(pkg_name)
            prerelease_version = __version__
            version = _compute_non_prerelease_version(prerelease_version)
        except (ImportError, metadata.PackageNotFoundError):
            pass
        else:
            return {
                "version": prerelease_version,
                "main_version": version,
                "package": display_name,
            }

    if __version__ is None:
        msg = f"Package not found from options {package_options}"
        raise ValueError(msg)
    return None


VERSION_INFO = _get_version_info()


def is_pre_release(v: str) -> bool:
    """判断版本是否为预发布版本。
    
    关键路径（三步）：
    1) 检查版本字符串是否包含预发布标签
    2) 返回是否存在预发布标签的结果
    3) 依据PEP 440中预发布段的定义
    
    异常流：无异常处理
    性能瓶颈：字符串搜索操作
    排障入口：检查版本字符串是否正确包含预发布标识
    """
    return any(label in v for label in ["a", "b", "rc"])


def is_nightly(v: str) -> bool:
    """判断版本是否为开发(夜间)版本。
    
    关键路径（单步）：
    1) 检查版本字符串是否包含'dev'标签
    
    异常流：无异常处理
    性能瓶颈：字符串搜索操作
    排障入口：检查版本字符串是否正确包含开发版本标识
    """
    return "dev" in v


def fetch_latest_version(package_name: str, *, include_prerelease: bool) -> str | None:
    """获取包的最新版本。
    
    关键路径（三步）：
    1) 格式化包名称并从PyPI获取版本信息
    2) 过滤预发布版本（根据参数决定）
    3) 返回最新的有效版本
    
    异常流：捕获所有异常并返回None
    性能瓶颈：网络请求和版本比较
    排障入口：检查包名称是否正确，网络连接是否正常
    """
    package_name = package_name.replace(" ", "-").lower()
    try:
        response = httpx.get(f"https://pypi.org/pypi/{package_name}/json")
        versions = response.json()["releases"].keys()
        valid_versions = [v for v in versions if include_prerelease or not is_pre_release(v)]
        if not valid_versions:
            return None  # Handle case where no valid versions are found
        return max(valid_versions, key=pkg_version.parse)

    except Exception:  # noqa: BLE001
        return None


def get_version_info():
    """获取版本信息。
    
    返回预先计算的版本信息。
    """
    return VERSION_INFO
