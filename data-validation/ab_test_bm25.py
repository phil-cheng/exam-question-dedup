"""BM25 与 8B 余弦在同一测试集（test_dataset.xlsx）上的重复识别率对比。

回答：BM25 单独当判定器，比 8B 余弦差多少？
  1. 真重复 9 对在两种分数下的分布与排名
  2. 各阈值下命中 / 灰色可见 / 误报（BM25 全量、BM25 Top50、余弦全量）
  3. 结构性漏检：换说法型（词面几乎不重叠）在 BM25 下是否天然低分

BM25 分数 = 自归一化 bm25_norm（与余弦同向可比：越大越像）。
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

# ---- 人工标注 ground truth（与 ab_test_recall.py 一致）----
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
# BM25 分数尺度比余弦低（只认词面重叠），阈值从 0.30 起步
THRESHOLDS = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


def code_pair(code2i: dict, a: str, b: str) -> tuple[int, int]:
    i, j = code2i[a], code2i[b]
    return (i, j) if i < j else (j, i)


def collect(idx_matrix: np.ndarray, norm_matrix: np.ndarray) -> dict[tuple[int, int], float]:
    """把 TopK 索引+分数矩阵转成 {无向对: 分数}，重复对取高分。"""
    out: dict[tuple[int, int], float] = {}
    n = idx_matrix.shape[0]
    for i in range(n):
        for col in range(idx_matrix.shape[1]):
            j = int(idx_matrix[i, col])
            if j < 0 or j == i:
                continue
            key = (i, j) if i < j else (j, i)
            out[key] = max(out.get(key, 0.0), float(norm_matrix[i, col]))
    return out


def rank_of(idx_matrix: np.ndarray, i: int, j: int) -> int:
    """j 相对 query i 的排序名次（1-based，不在候选则 n）。"""
    for r in range(idx_matrix.shape[1]):
        if int(idx_matrix[i, r]) == j:
            return r + 1
    return idx_matrix.shape[1]


def main() -> None:
    cfg = load_config()
    if not cfg.embed_enabled:
        print("未配置向量服务，无法跑语义对比。")
        return

    qs = load_questions(str(Path(__file__).resolve().parent / "test_dataset.xlsx"))
    texts = [q.search_text for q in qs]
    n = len(texts)
    code2i = {q.code: i for i, q in enumerate(qs)}
    print(f"题数 n = {n}，向量模型 = {cfg.embed_model}")

    tp_keys = {code_pair(code2i, a, b) for a, b in TRUE_DUP}
    gray_keys = {code_pair(code2i, a, b) for a, b in GRAY}

    # ---- 余弦 8B 全量 ----
    try:
        vecs = embed_texts(texts, cfg.embed_base_url, cfg.embed_model, cfg.embed_api_key)
    except EmbedError as exc:
        print("向量化失败:", exc)
        return
    cos_idx, cos_sim = cosine_neighbors(vecs, k=n)  # n=66，等于全量
    cos_full = collect(cos_idx, cos_sim)
    print(f"余弦 8B 维度 {vecs.shape[1]}，全量对 {len(cos_full)}")

    # ---- BM25 全量 + Top50 ----
    bm_idx, bm_norm = bm25_neighbors(texts, k=n)
    bm_full = collect(bm_idx, bm_norm)
    bm50 = collect(bm_idx[:, : min(50, n - 1)], bm_norm[:, : min(50, n - 1)])
    print(f"BM25 全量对 {len(bm_full)}，Top50 对 {len(bm50)}")

    # ---- 真重复 9 对明细 ----
    print("\n" + "=" * 108)
    print(f"{'真重复对':<14}{'形态':<10}{'BM25分':<8}{'余弦8B':<8}{'BM25排名':<9}{'余弦排名':<9}")
    print("-" * 108)
    tag_of = {
        "01": "标点/空白", "03": "换说法", "07": "符号公式",
        "06": "选项重排",
    }
    for (i, j) in sorted(tp_keys):
        a_code, b_code = qs[i].code, qs[j].code
        tag = tag_of.get(a_code[:2], "")
        print(
            f"{a_code}-{b_code:<9}{tag:<10}"
            f"{bm_full[(i, j)]:<8.3f}{cos_full[(i, j)]:<8.3f}"
            f"{rank_of(bm_idx, i, j):<9}{rank_of(cos_idx, i, j):<9}"
        )

    # ---- 各阈值命中率（真重复命中 / 灰色可见 / 误报）----
    def stats(score_map: dict[tuple[int, int], float], T: float):
        shown = {k for k, v in score_map.items() if v >= T}
        tp = len(shown & tp_keys)
        gy = len(shown & gray_keys)
        fp = len(shown) - tp - gy
        return f"{tp}/{gy}/{fp}"

    print("\n" + "=" * 108)
    print(f"{'阈值':<6}{'BM25全量 命中/灰/误':<20}{'BM25 Top50 命中/灰/误':<22}{'余弦8B全量 命中/灰/误':<22}")
    print("-" * 108)
    for T in THRESHOLDS:
        print(
            f"{T:<6}"
            f"{stats(bm_full, T):<20}"
            f"{stats(bm50, T):<22}"
            f"{stats(cos_full, T):<22}"
        )

    # ---- BM25 低分真重复（结构性漏检）----
    print("\nBM25 给分低于 0.50 的真重复对（词面不重叠 → 判定层天然漏）:")
    low = [k for k in tp_keys if bm_full[k] < 0.50]
    for (i, j) in sorted(low, key=lambda k: bm_full[k]):
        print(f"  {qs[i].code}-{qs[j].code}  bm25={bm_full[(i, j)]:.3f}  cos={cos_full[(i, j)]:.3f}")
    if not low:
        print("  （无）")


if __name__ == "__main__":
    main()
