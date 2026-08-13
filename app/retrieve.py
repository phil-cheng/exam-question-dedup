"""BM25 与精确余弦召回。只负责 TopK，不算融合分。"""

from __future__ import annotations

import numpy as np
import bm25s

from app.textutil import tokenize

TOP_K = 50


def bm25_neighbors(texts: list[str], k: int = TOP_K) -> tuple[np.ndarray, np.ndarray]:
    """
    对每道题取 BM25 TopK（不含自己）。
    返回 idx[n, k]、norm[n, k]，norm = score / 该题对自己的分。
    """
    tokenized = [tokenize(t) or ["_empty"] for t in texts]
    retriever = bm25s.BM25()
    retriever.index(tokenized, show_progress=False)

    n = len(texts)
    k_use = min(k + 1, n)  # 多取 1 个，用来拿「自己」
    queries = tokenized
    ids, scores = retriever.retrieve(queries, k=k_use, show_progress=False)

    out_idx = np.full((n, k), -1, dtype=np.int32)
    out_norm = np.zeros((n, k), dtype=np.float32)
    for i in range(n):
        row_ids = ids[i]
        row_scores = scores[i].astype(np.float32)
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
    """L2 归一化后分块点积 = 余弦。返回 idx[n,k]、sim[n,k]，不含自己。"""
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
        sim = x[start:end] @ x.T
        for r, gi in enumerate(range(start, end)):
            sim[r, gi] = -np.inf
        if n == 1:
            continue
        idx = np.argpartition(sim, -k_use, axis=1)[:, -k_use:]
        part = np.take_along_axis(sim, idx, axis=1)
        order = np.argsort(-part, axis=1)
        top_idx[start:end] = np.take_along_axis(idx, order, axis=1)
        top_sim[start:end] = np.take_along_axis(part, order, axis=1)
    # 余弦裁到 [0,1]，负值对查重无意义
    np.clip(top_sim, 0.0, 1.0, out=top_sim)
    return top_idx, top_sim
