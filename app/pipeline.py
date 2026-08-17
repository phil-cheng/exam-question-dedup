"""
查重主流程（只算一次，滑条事后过滤）：

    读表 → 尝试远程向量：成功 → 余弦 Top50 → 纯余弦判决
                        失败/未配置 → BM25 Top50 → BM25 相对分判决（托底）

两条路径互不混合：实测掺 BM25 进判决是减分、并集召回是零贡献，
依据见 docs/为何给余弦加BM25提升不了结果.md。
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
    extra: dict = field(default_factory=dict)

    def scored(self, threshold: float) -> list[tuple[PairResult, float]]:
        # 改阈值只走这里，不再分词 / 打向量
        out: list[tuple[PairResult, float]] = []
        for p in self.pairs:
            s = p.score(self.has_vectors)
            if s + 1e-12 >= threshold:
                out.append((p, s))
        out.sort(key=lambda x: x[1], reverse=True)
        return out


def _pair_key(i: int, j: int) -> tuple[int, int]:
    # 无向：同一对只存一次
    return (i, j) if i < j else (j, i)


def _topk_score_map(
    n: int, idx: np.ndarray, scores: np.ndarray
) -> dict[tuple[int, int], float]:
    """单路 TopK 索引+分数 → {无向对: 分数}，双向取高分去重。"""
    best: dict[tuple[int, int], float] = {}
    for i in range(n):
        for col in range(idx.shape[1]):
            j = int(idx[i, col])
            if j < 0 or j == i:
                continue
            key = _pair_key(i, j)
            val = float(scores[i, col])
            if val > best.get(key, 0.0):
                best[key] = val
    return best


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
    k_use = min(TOP_K, n - 1)

    # ---- 先试向量服务：成功走纯余弦；失败/未配置才回头算 BM25 托底 ----
    has_vectors = False
    fallback = ""
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
            has_vectors = True
        except EmbedError as exc:
            fallback = str(exc)

    if has_vectors:
        cos_idx, cos_sim = cosine_neighbors(vectors, k=k_use)
        pairs = [
            PairResult(i=i, j=j, cosine=s, bm25_norm=0.0)
            for (i, j), s in _topk_score_map(n, cos_idx, cos_sim).items()
        ]
    else:
        prog("正在计算 BM25（未配置向量服务，文本托底）", 0, n)
        bm25_idx, bm25_norm = bm25_neighbors(texts, k=k_use)
        pairs = [
            PairResult(i=i, j=j, cosine=None, bm25_norm=s)
            for (i, j), s in _topk_score_map(n, bm25_idx, bm25_norm).items()
        ]

    prog("完成", n, n)
    return RunResult(
        questions=questions,
        pairs=pairs,
        has_vectors=has_vectors,
        fallback_reason=fallback,
    )
