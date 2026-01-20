"""
模块名称：知识库评分与枚举工具

本模块提供轻量的文本相关性评分与知识库目录枚举能力，供检索前后处理使用。主要功能包括：
- 基于简单分词的 TF-IDF 与 BM25 评分
- 基于用户隔离的知识库目录列表获取

关键组件：
- `compute_tfidf`：查询词 TF-IDF 评分
- `compute_bm25`：查询词 BM25 评分
- `get_knowledge_bases`：用户知识库目录枚举

设计背景：无需引入重型检索依赖时，提供可用的轻量评分与目录查询。
使用场景：小规模知识库检索、前置筛选或调试验证。
注意事项：分词采用空白切分，语言与标点处理能力有限。
"""

import math
from collections import Counter
from pathlib import Path
from uuid import UUID

from langflow.services.database.models.user.crud import get_user_by_id
from langflow.services.deps import session_scope


def compute_tfidf(documents: list[str], query_terms: list[str]) -> list[float]:
    """计算查询词在文档集合中的 TF-IDF 得分

    契约：输入 `documents`/`query_terms`（字符串列表）；输出与 `documents` 等长的 `list[float]`；
    副作用：无；失败语义：`documents` 为空返回空列表，文档长度为 0 的 TF 视为 0。
    关键路径（三步）：
    1) 小写+空白切分得到 token
    2) 统计查询词在各文档的文档频次
    3) 逐文档累计 TF * IDF
    决策：使用空白切分而非引入分词库
    问题：需要轻量评分以避免新增依赖
    方案：`str.lower().split()` 作为近似分词
    代价：不处理标点与语言差异，评分精度受限
    重评：当多语言或分词质量成为主要瓶颈时
    """
    tokenized_docs = [doc.lower().split() for doc in documents]
    n_docs = len(documents)

    document_frequencies = {}
    for term in query_terms:
        document_frequencies[term] = sum(1 for doc in tokenized_docs if term.lower() in doc)

    scores = []

    for doc_tokens in tokenized_docs:
        doc_score = 0.0
        doc_length = len(doc_tokens)
        term_counts = Counter(doc_tokens)

        for term in query_terms:
            term_lower = term.lower()

            tf = term_counts[term_lower] / doc_length if doc_length > 0 else 0

            idf = math.log(n_docs / document_frequencies[term]) if document_frequencies[term] > 0 else 0

            doc_score += tf * idf

        scores.append(doc_score)

    return scores


def compute_bm25(documents: list[str], query_terms: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """计算查询词在文档集合中的 BM25 得分

    契约：输入 `documents`/`query_terms`，可选参数 `k1`/`b`；输出与 `documents` 等长的 `list[float]`；
    副作用：无；失败语义：当平均文档长度为 0 时返回全 0；分母为 0 时单项得分置 0。
    关键路径（三步）：
    1) 小写+空白切分得到 token，并计算平均文档长度
    2) 统计查询词在各文档的文档频次
    3) 按 BM25 公式累加每个查询词的得分
    决策：平均文档长度为 0 时直接返回全 0
    问题：空文档集合会导致长度归一化失效
    方案：短路返回，避免除零与无意义评分
    代价：无法区分空文档之间的相对差异
    重评：当需要对空文档做区分性处理时
    """
    tokenized_docs = [doc.lower().split() for doc in documents]
    n_docs = len(documents)

    avg_doc_length = sum(len(doc) for doc in tokenized_docs) / n_docs if n_docs > 0 else 0

    if avg_doc_length == 0:
        return [0.0] * n_docs

    document_frequencies = {}
    for term in query_terms:
        document_frequencies[term] = sum(1 for doc in tokenized_docs if term.lower() in doc)

    scores = []

    for doc_tokens in tokenized_docs:
        doc_score = 0.0
        doc_length = len(doc_tokens)
        term_counts = Counter(doc_tokens)

        for term in query_terms:
            term_lower = term.lower()

            tf = term_counts[term_lower]

            idf = math.log(n_docs / document_frequencies[term]) if document_frequencies[term] > 0 else 0

            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (doc_length / avg_doc_length))

            term_score = 0 if denominator == 0 else idf * (numerator / denominator)

            doc_score += term_score

        scores.append(doc_score)

    return scores


async def get_knowledge_bases(kb_root: Path, user_id: UUID | str) -> list[str]:
    """获取当前用户可见的知识库目录列表

    契约：输入 `kb_root` 与 `user_id`；输出知识库名称列表（目录名）；
    副作用：访问数据库读取用户信息；失败语义：`user_id` 缺失或无效、用户不存在时抛 `ValueError`。
    关键路径（三步）：
    1) 校验 `kb_root` 是否存在
    2) 解析 `user_id` 并获取用户 `username`
    3) 枚举用户目录下的子目录（忽略隐藏目录）
    决策：按 `username` 作为知识库的用户隔离目录
    问题：需要在文件系统层面隔离不同用户的知识库
    方案：使用 `kb_root/<username>` 作为目录前缀
    代价：用户名变更会影响历史路径
    重评：当引入稳定的用户目录标识（如 UID）时
    """
    if not kb_root.exists():
        return []

    async with session_scope() as db:
        if not user_id:
            msg = "User ID is required for fetching knowledge bases."
            raise ValueError(msg)
        user_id = UUID(user_id) if isinstance(user_id, str) else user_id
        current_user = await get_user_by_id(db, user_id)
        if not current_user:
            msg = f"User with ID {user_id} not found."
            raise ValueError(msg)
        kb_user = current_user.username
    kb_path = kb_root / kb_user

    if not kb_path.exists():
        return []

    return [str(d.name) for d in kb_path.iterdir() if not d.name.startswith(".") and d.is_dir()]
