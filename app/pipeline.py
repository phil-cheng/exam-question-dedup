"""一次计算：读表 → BM25 → 可选向量 → 并集候选 → 存原始分。"""

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
        out: list[tuple[PairResult, float]] = []
        for p in self.pairs:
            s = p.score(self.alpha, self.has_vectors)
            if s + 1e-12 >= threshold:
                out.append((p, s))
        out.sort(key=lambda x: x[1], reverse=True)
        return out


def _pair_key(i: int, j: int) -> tuple[int, int]:
    return (i, j) if i < j else (j, i)


def _collect_pairs(
    n: int,
    bm25_idx: np.ndarray,
    bm25_norm: np.ndarray,
    cos_idx: np.ndarray | None,
    vectors: np.ndarray | None,
) -> list[PairResult]:
    """无向题对：并集召回；有向量时对每对补算精确余弦。"""
    bm25_map: dict[tuple[int, int], float] = {}
    keys: set[tuple[int, int]] = set()

    for i in range(n):
        for col in range(bm25_idx.shape[1]):
            j = int(bm25_idx[i, col])
            if j < 0 or j == i:
                continue
            key = _pair_key(i, j)
            keys.add(key)
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
    if cfg.embed_enabled:
        prog("正在请求 embedding", 0, n)
        try:
            vectors = embed_texts(
                texts,
                cfg.embed_base_url,
                cfg.embed_model,
                cfg.embed_api_key,
                on_progress=lambda c, t, m: prog(m, c, t),
            )
            prog("正在计算余弦", n, n)
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
