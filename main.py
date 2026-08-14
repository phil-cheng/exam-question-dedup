"""试题文义查重入口。无参数开界面；--cli <xls> 打命令行结果便于验收。"""

from __future__ import annotations

import argparse
import sys


def _cli(path: str, threshold: float) -> int:
    from app.config import load_config
    from app.excel_io import TemplateError
    from app.pipeline import run_dedup

    cfg = load_config()
    try:
        result = run_dedup(path, cfg, on_progress=lambda m, c, t: print(m, f"{c}/{t}" if t else ""))
    except TemplateError as exc:
        print(exc, file=sys.stderr)
        return 2
    mode = "语义（余弦）" if result.has_vectors else "仅文本（BM25）"
    if result.fallback_reason:
        print("回退:", result.fallback_reason)
    rows = result.scored(threshold)
    print(f"模式={mode} 题量={len(result.questions)} 阈值={threshold:.2f} 命中={len(rows)}")
    for pair, score in rows[:50]:
        a = result.questions[pair.i]
        b = result.questions[pair.j]
        print(f"{score:.3f}  {a.code} ↔ {b.code}  | {a.stem[:24]} || {b.stem[:24]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="试题文义查重")
    parser.add_argument("--cli", metavar="XLS", help="命令行跑一份表并打印题对")
    parser.add_argument("--threshold", type=float, default=0.75)
    args = parser.parse_args()
    if args.cli:
        return _cli(args.cli, args.threshold)
    from app.ui import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
