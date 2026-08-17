"""BM25 与精确余弦召回。只负责 TopK，不算融合分。"""

from __future__ import annotations

import numpy as np
import bm25s

from app.textutil import tokenize

TOP_K = 50  # 万级足够（66 题集真重复余弦排名 ≤6）；若某知识点簇特别肥致字面近重复掉出，调到 100 即可，不要恢复 BM25 并集（见 docs/为何给余弦加BM25提升不了结果.md）


def bm25_neighbors(texts: list[str], k: int = TOP_K) -> tuple[np.ndarray, np.ndarray]:
    """
    每道题当 query，取词面最像的 k 道（不含自己）。
    原始 BM25 跨题不可比，所以除以「自己对自己的分」压到 [0,1]。
    """
    tokenized = [tokenize(t) or ["_empty"] for t in texts]
    retriever = bm25s.BM25()
    retriever.index(tokenized, show_progress=False)

    n = len(texts)
    k_use = min(k + 1, n)  # 多取 1 个：结果里通常含自己，用来当归一化分母
    ids, scores = retriever.retrieve(tokenized, k=k_use, show_progress=False)

    out_idx = np.full((n, k), -1, dtype=np.int32)
    out_norm = np.zeros((n, k), dtype=np.float32)
    for i in range(n):
        row_ids = ids[i]
        row_scores = scores[i].astype(np.float32)
        # 自己通常是第 1 名；万一没进 TopK，用该行最大分兜底
        self_score = 0.0
        for doc_id, sc in zip(row_ids, row_scores):
            if int(doc_id) == i:
                self_score = float(sc)
                break
        if self_score <= 1e-8:
            self_score = float(np.max(row_scores)) if row_scores.size else 1.0
            if self_score <= 1e-8:
                self_score = 1.0
        kept = 0
        for doc_id, sc in zip(row_ids, row_scores):
            j = int(doc_id)
            if j == i or j < 0:
                continue
            out_idx[i, kept] = j
            out_norm[i, kept] = min(1.0, max(0.0, float(sc) / self_score))
            kept += 1
            if kept >= k:
                break
    return out_idx, out_norm


def cosine_neighbors(
    vectors: np.ndarray, k: int = TOP_K, batch_size: int = 512
) -> tuple[np.ndarray, np.ndarray]:
    """
    精确余弦 TopK，不做 ANN。
    向量先 L2 归一化，点积即余弦；分块算是避免物化 n×n 全矩阵。
    """
    x = np.asarray(vectors, dtype=np.float32)
    if x.ndim != 2:
        raise ValueError("向量矩阵形状应为 (n, dim)。")
    n = x.shape[0]
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    x = x / norms
    k_use = min(k, max(n - 1, 1))
    top_idx = np.full((n, k_use), -1, dtype=np.int32)
    top_sim = np.zeros((n, k_use), dtype=np.float32)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        sim = x[start:end] @ x.T  # (batch, n)
        for r, gi in enumerate(range(start, end)):
            sim[r, gi] = -np.inf  # 去掉自己
        if n == 1:
            continue
        # argpartition 只保证 TopK 无序入围，再对这 K 个排序
        idx = np.argpartition(sim, -k_use, axis=1)[:, -k_use:]
        part = np.take_along_axis(sim, idx, axis=1)
        order = np.argsort(-part, axis=1)
        top_idx[start:end] = np.take_along_axis(idx, order, axis=1)
        top_sim[start:end] = np.take_along_axis(part, order, axis=1)
    np.clip(top_sim, 0.0, 1.0, out=top_sim)  # 负余弦对查重无意义
    return top_idx, top_sim
