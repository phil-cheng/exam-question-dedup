"""一次性导出 BM25 + 三个 Qwen3-Embedding 尺寸在全量题对上的分数，落成 scores.csv。

之后的阈值 / 混合 / 召回 / 排名推演都读这份 CSV 做代数，不再调 API。
注意：API 分数有 ±0.001 级抖动，推演阈值边界要留余量，不钉死最后一位小数。

CSV 结构（一行一对，n=66 → 2145 行）：
    code_a, code_b, label, bm25, cos_0p6b, cos_4b, cos_8b
    label ∈ true / gray / none（见 ground_truth.py）
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.embed import EmbedError, embed_texts
from app.excel_io import load_questions
from app.retrieve import bm25_neighbors, cosine_neighbors
from ground_truth import GRAY, TRUE_DUP

# 硅基流动在线模型名（本地 Ollama 可换成 qwen3-embedding:0.6b 等）
MODELS = [
    ("cos_0p6b", "Qwen/Qwen3-Embedding-0.6B"),
    ("cos_4b", "Qwen/Qwen3-Embedding-4B"),
    ("cos_8b", "Qwen/Qwen3-Embedding-8B"),
]


def label_of(code_a: str, code_b: str) -> str:
    pair = (code_a, code_b)
    if pair in TRUE_DUP:
        return "true"
    if pair in GRAY:
        return "gray"
    return "none"


def full_cosine(vecs: np.ndarray) -> dict[tuple[int, int], float]:
    """全量 i<j 对的精确余弦。n 小，直接算对称矩阵。"""
    x = np.asarray(vecs, dtype=np.float32)
    x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    sim = x @ x.T  # (n, n)
    n = x.shape[0]
    out: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            out[(i, j)] = float(np.clip(sim[i, j], 0.0, 1.0))
    return out


def main() -> None:
    cfg = load_config()
    if not cfg.embed_enabled:
        print("未配置向量服务，无法导出。")
        return

    qs = load_questions(str(Path(__file__).resolve().parent / "test_dataset.xlsx"))
    texts = [q.search_text for q in qs]
    n = len(texts)
    print(f"题数 n = {n}，全量题对 {n * (n - 1) // 2}")

    # ---- BM25 全量自归一化分 ----
    bm_idx, bm_norm = bm25_neighbors(texts, k=n)
    bm_full: dict[tuple[int, int], float] = {}
    for i in range(n):
        for col in range(n - 1):
            j = int(bm_idx[i, col])
            if j < 0 or j == i:
                continue
            key = (i, j) if i < j else (j, i)
            bm_full[key] = max(bm_full.get(key, 0.0), float(bm_norm[i, col]))
    print(f"BM25 完成，对 {len(bm_full)}")

    # ---- 三个模型的余弦 ----
    cos_by_model: dict[str, dict[tuple[int, int], float]] = {}
    for col_name, model in MODELS:
        try:
            vecs = embed_texts(texts, cfg.embed_base_url, model, cfg.embed_api_key)
        except EmbedError as exc:
            print(f"模型 {model} 向量化失败，中止以免生成缺列数据：{exc}")
            return
        cos_by_model[col_name] = full_cosine(vecs)
        print(f"{model} 完成，维度 {vecs.shape[1]}")

    # ---- 写 CSV ----
    out = Path(__file__).resolve().parent / "scores.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["code_a", "code_b", "label", "bm25", "cos_0p6b", "cos_4b", "cos_8b"])
        for i in range(n):
            for j in range(i + 1, n):
                key = (i, j)
                writer.writerow(
                    [
                        qs[i].code,
                        qs[j].code,
                        label_of(qs[i].code, qs[j].code),
                        f"{bm_full.get(key, 0.0):.6f}",
                        f"{cos_by_model['cos_0p6b'][key]:.6f}",
                        f"{cos_by_model['cos_4b'][key]:.6f}",
                        f"{cos_by_model['cos_8b'][key]:.6f}",
                    ]
                )
    print(f"已写入 {out}，共 {n * (n - 1) // 2} 行")


if __name__ == "__main__":
    main()
