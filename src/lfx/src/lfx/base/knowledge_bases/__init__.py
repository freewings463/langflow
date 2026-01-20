"""
模块名称：知识库基础工具包入口

本模块用于聚合知识库相关工具函数的导出，供上层组件统一导入。主要功能包括：
- 作为 `lfx.base.knowledge_bases` 的包级入口
- 暴露 TF-IDF、BM25 评分与知识库目录枚举能力

关键组件：
- `knowledge_base_utils` 中的 `compute_tfidf`/`compute_bm25`/`get_knowledge_bases`

设计背景：知识库工具被多个子模块复用，需要稳定的导入路径。
使用场景：上层检索或索引流程直接引用本包导出的函数。
注意事项：本包仅做符号导出，不包含运行时逻辑。
"""

from .knowledge_base_utils import compute_bm25, compute_tfidf, get_knowledge_bases

__all__ = ["compute_bm25", "compute_tfidf", "get_knowledge_bases"]
