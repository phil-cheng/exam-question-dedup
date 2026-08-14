"""召回策略 A/B 测试：对比「余弦Top50 / BM25∪余弦Top50 / 余弦Top200」在纯 cos 判决下的差异。

回答的问题：
1. 真重复（人工标注 10 对）在三种召回 + 纯 cos 判决下，各阈值能召回几对？
2. 灰色地带（字面像但考点存疑，10 对）在三种策略下的可见性（供人工判断）。
3. BM25 并集比余弦 Top50 多捞了哪些对？它们的精确 cos 值过不过线？（判断并集到底补了几对）
4. 余弦 Top200 比 Top50 多捞哪些？同理。
5. 真重复对在余弦召回里的排名——有没有被 TopK 截断漏掉、但 cos 值其实够高的。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.embed import EmbedError, embed_texts
from app.excel_io import load_questions
from app.retrieve import bm25_neighbors, cosine_neighbors

# ---- 人工标注 ground truth（code 对）----
TRUE_DUP = {  # 真重复：考点相同，人认为该被查出
    ("0101", "0102"), ("0101", "0103"), ("0102", "0103"),
    ("0301", "0302"), ("0303", "0304"), ("0305", "0306"),
    ("0701", "0702"), ("0703", "0704"),
    ("0601", "0602"),
}
GRAY = {  # 灰色：字面像但考点存疑，理想是被显示出来供人工判断
    ("0001", "0011"), ("0401", "0402"), ("0403", "0404"),
    ("0501", "0502"), ("0501", "0503"), ("0502", "0503"),
    ("0601", "0603"), ("0705", "0706"), ("0003", "0004"),
}
THRESHOLDS = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


def norm_vectors(vecs: np.ndarray) -> np.ndarray:
    x = np.asarray(vecs, dtype=np.float32)
    norms = np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    return x / norms


def pairs_from(idx_matrix: np.ndarray, k: int) -> set[tuple[int, int]]:
    """把 TopK 索引矩阵转成无向对集合。"""
    keys: set[tuple[int, int]] = set()
    for i in range(idx_matrix.shape[0]):
        for col in range(k):
            j = int(idx_matrix[i, col])
            if j < 0 or j == i:
                continue
            keys.add((i, j) if i < j else (j, i))
    return keys


def code_pair(code2i: dict, a: str, b: str) -> tuple[int, int]:
    i, j = code2i[a], code2i[b]
    return (i, j) if i < j else (j, i)


def main() -> None:
    cfg = load_config()
    if not cfg.embed_enabled:
        print("未配置向量服务，无法跑语义实验。")
        return

    qs = load_questions(str(Path(__file__).resolve().parent / "test_dataset.xlsx"))
    texts = [q.search_text for q in qs]
    n = len(texts)
    code2i = {q.code: i for i, q in enumerate(qs)}
    print(f"题数 n = {n}")

    tp_keys = {code_pair(code2i, a, b) for a, b in TRUE_DUP}
    gray_keys = {code_pair(code2i, a, b) for a, b in GRAY}
    assert all(len(p) == 2 and p[0] < p[1] for p in tp_keys | gray_keys)

    # ---- 向量化 ----
    try:
        vecs = embed_texts(texts, cfg.embed_base_url, cfg.embed_model, cfg.embed_api_key)
    except EmbedError as exc:
        print("向量化失败:", exc)
        return
    unit = norm_vectors(vecs)
    print(f"向量维度 {vecs.shape[1]}，耗时见下")

    def cos_of(i: int, j: int) -> float:
        return float(np.clip(unit[i] @ unit[j], 0.0, 1.0))

    # ---- 三种召回 ----
    cos_idx200, cos_sim200 = cosine_neighbors(vecs, k=200)  # n=66 < 200，实际等于全量
    bm25_idx, _ = bm25_neighbors(texts, k=50)

    r50 = pairs_from(cos_idx200, 50)
    r200 = pairs_from(cos_idx200, min(200, n - 1))
    rbm = r50 | pairs_from(bm25_idx, 50)

    print(f"\n候选对规模：余弦Top50={len(r50)}，余弦Top200={len(r200)}，BM25∪余弦Top50={len(rbm)}")
    print(f"BM25并集比Top50多：{len(rbm - r50)} 对；Top200比Top50多：{len(r200 - r50)} 对")

    # ---- 各阈值下三种策略的列表构成 ----
    print("\n" + "=" * 110)
    print(f"{'阈值':<6}{'策略':<18}{'显示对数':<8}{'真重复命中':<10}{'灰色可见':<10}{'误报(无关)':<10}")
    print("-" * 110)
    for T in THRESHOLDS:
        for name, keys in (("cosTop50", r50), ("BM25∪cosTop50", rbm), ("cosTop200", r200)):
            shown = [k for k in keys if cos_of(*k) >= T]
            tp = [k for k in shown if k in tp_keys]
            gy = [k for k in shown if k in gray_keys]
            fp = len(shown) - len(tp) - len(gy)
            print(f"{T:<6}{name:<18}{len(shown):<8}{len(tp):<10}{len(gy):<10}{fp:<10}")
    print("=" * 110)

    # ---- 真重复对的余弦排名（是否被 TopK 截断）----
    print("\n真重复对在余弦排序中的排名（50 名内=Top50 能召回）:")
    rank_of: dict[tuple[int, int], int] = {}
    for i in range(n):
        order = np.argsort(-cos_sim200[i]) if cos_sim200 is not None else []
        for r, j in enumerate(order, start=1):
            j = int(cos_idx200[i, r - 1])
            if j == i:
                continue
            key = (i, j) if i < j else (j, i)
            if key not in rank_of:
                rank_of[key] = r
    rank_rows = []
    for (i, j) in tp_keys:
        r = rank_of.get((i, j), float("inf"))
        rank_rows.append((r, qs[i].code, qs[j].code, cos_of(i, j)))
    rank_rows.sort()
    for r, a, b, c in rank_rows:
        flag = "  <- 排名>50 被 Top50 截断" if r > 50 else ""
        print(f"  {a}-{b}  cos={c:.3f}  排名~{r}{flag}")

    # ---- BM25 并集额外捞的对：cos 分布 ----
    print("\n只被 BM25 捞到（未进余弦Top50）的对，其精确 cos：")
    extra_bm = sorted(rbm - r50, key=lambda k: -cos_of(*k))
    for (i, j) in extra_bm:
        tag = "真重复" if (i, j) in tp_keys else ("灰色" if (i, j) in gray_keys else "无关")
        print(f"  {qs[i].code}-{qs[j].code}  cos={cos_of(i, j):.3f}  [{tag}]")

    # ---- Top200 额外捞的对 ----
    print("\n只被 Top200 捞到（未进Top50）的对，其精确 cos：")
    extra_200 = sorted(r200 - r50, key=lambda k: -cos_of(*k))
    for (i, j) in extra_200[:20]:
        tag = "真重复" if (i, j) in tp_keys else ("灰色" if (i, j) in gray_keys else "无关")
        print(f"  {qs[i].code}-{qs[j].code}  cos={cos_of(i, j):.3f}  [{tag}]")

    # ---- 真重复里有没有被两路都漏的 ----
    all_cand = rbm | r200
    missed = [k for k in tp_keys if k not in all_cand]
    if missed:
        print("\n!! 有真重复对完全没进任何候选（两路召回都漏）：")
        for (i, j) in missed:
            print(f"  {qs[i].code}-{qs[j].code}  cos={cos_of(i, j):.3f}")
    else:
        print("\n所有真重复对都至少被一路召回。")


if __name__ == "__main__":
    main()
