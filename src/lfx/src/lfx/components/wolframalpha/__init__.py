"""
模块名称：WolframAlpha 组件导出门面

模块目的：统一导出 WolframAlpha 组件，供组件发现与注册使用。
使用场景：在组件扫描阶段暴露 `WolframAlphaAPIComponent`。
主要功能包括：
- 导出 `WolframAlphaAPIComponent` 以供外部访问

关键组件：
- `WolframAlphaAPIComponent`：WolframAlpha API 工具组件

设计背景：该目录仅包含单一组件，采用直接导出以简化导入路径。
注意：若后续新增多个组件，可切换为延迟导入模式以降低依赖加载成本。
"""

from .wolfram_alpha_api import WolframAlphaAPIComponent

__all__ = ["WolframAlphaAPIComponent"]
