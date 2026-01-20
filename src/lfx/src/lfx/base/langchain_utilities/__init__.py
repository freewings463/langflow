"""
模块名称：LangChain 工具集成包

本模块提供 LangChain 工具相关的基础能力与常量集合，主要用于组件与工具链之间的接口对齐。主要功能包括：
- 定义 LangChain 工具组件的统一基类
- 提供 Spider 等组件使用的模式常量

关键组件：
- `model.py`：LangChain 工具组件基类与契约
- `spider_constants.py`：Spider 模式常量

设计背景：将 LangChain 相关能力集中管理，降低组件散落导致的契约不一致风险。
注意事项：本包仅提供基础契约与常量，不包含具体业务实现。
"""
