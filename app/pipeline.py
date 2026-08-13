"""
查重主流程（只算一次，滑条事后过滤）：

    读表 → BM25 Top50 →（可选）远程向量 + 余弦 Top50
         → 两路并集做成无向题对 → 每对存原始余弦 / BM25
         → 界面按 score() 卡阈值

向量失败不中断，回退纯 BM25。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from app.config import AppConfig
from app.embed import EmbedError, embed_texts
from app.excel_io import load_questions
from app.models import PairResult, Question
from app.retrieve import TOP_K, bm25_neighbors, cosine_neighbors

ProgressFn = Callable[[str, int, int], None]


@dataclass
class RunResult:
    questions: list[Question]
    pairs: list[PairResult]
    has_vectors: bool
    fallback_reason: str = ""
    alpha: float = 0.7
    extra: dict = field(default_factory=dict)

    def scored(self, threshold: float) -> list[tuple[PairResult, float]]:
        # 改阈值只走这里，不再分词 / 打向量
        out: list[tuple[PairResult, float]] = []
        for p in self.pairs:
            s = p.score(self.alpha, self.has_vectors)
            if s + 1e-12 >= threshold:
                out.append((p, s))
        out.sort(key=lambda x: x[1], reverse=True)
        return out


def _pair_key(i: int, j: int) -> tuple[int, int]:
    # 无向：同一对只存一次
    return (i, j) if i < j else (j, i)


def _collect_pairs(
    n: int,
    bm25_idx: np.ndarray,
    bm25_norm: np.ndarray,
    cos_idx: np.ndarray | None,
    vectors: np.ndarray | None,
) -> list[PairResult]:
    """
    候选 = BM25 TopK ∪ 余弦 TopK（无向，A-B 与 B-A 只留一条）。
    只被一路捞到的也留下：改写题靠向量，改两词靠 BM25。
    有向量时对并集里每一对重算精确余弦，避免「只在 BM25 里的对」没有语义分。
    """
    bm25_map: dict[tuple[int, int], float] = {}
    keys: set[tuple[int, int]] = set()

    for i in range(n):
        for col in range(bm25_idx.shape[1]):
            j = int(bm25_idx[i, col])
            if j < 0 or j == i:
                continue
            key = _pair_key(i, j)
            keys.add(key)
            # 两个方向各有一个归一化分，取较大的，避免「A 看 B 很像、B 看 A 一般」
            val = float(bm25_norm[i, col])
            if val > bm25_map.get(key, 0.0):
                bm25_map[key] = val

    if cos_idx is not None:
        for i in range(n):
            for col in range(cos_idx.shape[1]):
                j = int(cos_idx[i, col])
                if j < 0 or j == i:
                    continue
                keys.add(_pair_key(i, j))

    unit = None
    if vectors is not None:
        x = np.asarray(vectors, dtype=np.float32)
        norms = np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
        unit = x / norms

    pairs: list[PairResult] = []
    for i, j in keys:
        cosine = None
        if unit is not None:
            cosine = float(np.clip(unit[i] @ unit[j], 0.0, 1.0))
        pairs.append(
            PairResult(
                i=i,
                j=j,
                cosine=cosine,
                # 只被余弦捞到、BM25 未进 TopK：词面分记 0，综合分 = α·余弦
                bm25_norm=bm25_map.get((i, j), 0.0),
            )
        )
    return pairs


def run_dedup(
    excel_path: str,
    cfg: AppConfig,
    on_progress: ProgressFn | None = None,
) -> RunResult:
    def prog(msg: str, cur: int = 0, total: int = 0) -> None:
        if on_progress:
            on_progress(msg, cur, total)

    prog("正在读取 Excel")
    questions = load_questions(excel_path)
    texts = [q.search_text for q in questions]
    n = len(questions)

    prog("正在计算 BM25", 0, n)
    bm25_idx, bm25_norm = bm25_neighbors(texts, k=min(TOP_K, n - 1))

    has_vectors = False
    fallback = ""
    cos_idx = None
    vectors = None
    # 没配服务或请求失败：has_vectors=False，后面 score() 自动只用 BM25
    if cfg.embed_enabled:
        prog("正在调用向量服务", 0, n)
        try:
            vectors = embed_texts(
                texts,
                cfg.embed_base_url,
                cfg.embed_model,
                cfg.embed_api_key,
                on_progress=lambda c, t, m: prog(m, c, t),
            )
            prog("正在计算语义相似度", n, n)
            cos_idx, _ = cosine_neighbors(vectors, k=min(TOP_K, n - 1))
            has_vectors = True
        except EmbedError as exc:
            fallback = str(exc)
            vectors = None
            has_vectors = False

    prog("正在融合候选")
    pairs = _collect_pairs(n, bm25_idx, bm25_norm, cos_idx, vectors)
    prog("完成", n, n)
    return RunResult(
        questions=questions,
        pairs=pairs,
        has_vectors=has_vectors,
        fallback_reason=fallback,
        alpha=cfg.alpha,
    )
