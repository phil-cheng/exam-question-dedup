"""1000 题压测：向量余弦 vs BM25 托底。

测什么：
    1. 两条路径能不能跑完
    2. 分阶段耗时
    3. 本进程 CPU / 内存峰值
    4. 默认阈值下命中量，以及埋点近重复召回

用法（在项目根）：
    python data-validation/stress/stress_test.py
    python data-validation/stress/stress_test.py --skip-embed
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

STRESS_DIR = Path(__file__).resolve().parent
ROOT = STRESS_DIR.parent.parent
sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.embed import EmbedError, embed_texts
from app.excel_io import load_questions
from app.models import PairResult
from app.pipeline import RunResult, _topk_score_map
from app.retrieve import TOP_K, bm25_neighbors, cosine_neighbors


@dataclass
class PhaseStat:
    name: str
    seconds: float
    rss_mb_after: float


@dataclass
class PathReport:
    name: str
    ok: bool
    error: str
    n_questions: int
    n_pairs: int
    hits_075: int
    hits_060: int
    planted_total: int
    planted_hit_075: dict
    planted_hit_060: dict
    phases: list[PhaseStat]
    wall_s: float
    cpu_s: float
    peak_rss_mb: float
    avg_cpu_pct: float


class ResourceMonitor:
    """采样当前进程 RSS 与 CPU。"""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self.peak_rss = 0
        self.samples = 0
        self.cpu_sum = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = psutil.Process()
        self._proc.cpu_percent(None)  # 预热，避免首个样本为 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            rss = self._proc.memory_info().rss
            if rss > self.peak_rss:
                self.peak_rss = rss
            self.cpu_sum += self._proc.cpu_percent(None)
            self.samples += 1

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        rss = self._proc.memory_info().rss
        if rss > self.peak_rss:
            self.peak_rss = rss

    @property
    def peak_mb(self) -> float:
        return self.peak_rss / (1024 * 1024)

    @property
    def avg_cpu(self) -> float:
        return self.cpu_sum / self.samples if self.samples else 0.0

    def rss_mb(self) -> float:
        return self._proc.memory_info().rss / (1024 * 1024)


def _load_embed_cfg() -> AppConfig:
    # 打包后用户把模型配在 dist/config.json，压测跟这份走
    path = ROOT / "dist" / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig(
        embed_base_url=str(raw.get("embed_base_url", "") or ""),
        embed_model=str(raw.get("embed_model", "") or ""),
        embed_api_key=str(raw.get("embed_api_key", "") or ""),
    )


def _load_meta(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _planted_hits(
    result: RunResult, planted: list[dict], threshold: float
) -> dict[str, dict]:
    code_to_idx = {q.code: i for i, q in enumerate(result.questions)}
    score_of: dict[tuple[int, int], float] = {}
    for p in result.pairs:
        score_of[(p.i, p.j)] = p.score(result.has_vectors)
        score_of[(p.j, p.i)] = p.score(result.has_vectors)

    by_kind: dict[str, list[bool]] = {}
    for item in planted:
        a, b, kind = item["a"], item["b"], item["kind"]
        ia, ib = code_to_idx.get(a), code_to_idx.get(b)
        hit = False
        if ia is not None and ib is not None:
            s = score_of.get((ia, ib))
            hit = s is not None and s + 1e-12 >= threshold
        by_kind.setdefault(kind, []).append(hit)

    out = {}
    for kind, flags in by_kind.items():
        out[kind] = {"total": len(flags), "hit": sum(flags)}
    out["all"] = {
        "total": sum(v["total"] for v in out.values()),
        "hit": sum(v["hit"] for v in out.values()),
    }
    return out


def _count_hits(result: RunResult, threshold: float) -> int:
    return len(result.scored(threshold))


def run_bm25(xlsx: Path, planted: list[dict]) -> PathReport:
    mon = ResourceMonitor()
    mon.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    phases: list[PhaseStat] = []
    err = ""
    ok = True
    result: RunResult | None = None
    try:
        t0 = time.perf_counter()
        questions = load_questions(xlsx)
        phases.append(PhaseStat("读 Excel", time.perf_counter() - t0, mon.rss_mb()))

        texts = [q.search_text for q in questions]
        n = len(questions)
        k_use = min(TOP_K, n - 1)

        t0 = time.perf_counter()
        bm25_idx, bm25_norm = bm25_neighbors(texts, k=k_use)
        phases.append(PhaseStat("BM25 分词+建索引+检索", time.perf_counter() - t0, mon.rss_mb()))

        pairs = [
            PairResult(i=i, j=j, cosine=None, bm25_norm=s)
            for (i, j), s in _topk_score_map(n, bm25_idx, bm25_norm).items()
        ]
        result = RunResult(questions=questions, pairs=pairs, has_vectors=False)
    except Exception as exc:  # 压测要记下失败原因，不能 silently pass
        ok = False
        err = f"{type(exc).__name__}: {exc}"
        result = RunResult(questions=[], pairs=[], has_vectors=False)

    mon.stop()
    return _to_report(
        "BM25 托底", ok, err, result, planted, phases,
        time.perf_counter() - wall0, time.process_time() - cpu0, mon,
    )


def run_embed(xlsx: Path, planted: list[dict], cfg: AppConfig) -> PathReport:
    mon = ResourceMonitor()
    mon.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    phases: list[PhaseStat] = []
    err = ""
    ok = True
    result: RunResult | None = None
    try:
        t0 = time.perf_counter()
        questions = load_questions(xlsx)
        phases.append(PhaseStat("读 Excel", time.perf_counter() - t0, mon.rss_mb()))

        texts = [q.search_text for q in questions]
        n = len(questions)
        k_use = min(TOP_K, n - 1)

        def on_prog(cur: int, total: int, msg: str) -> None:
            if cur == 0 or cur == total or cur % 128 == 0:
                print(f"    {msg} {cur}/{total}", flush=True)

        t0 = time.perf_counter()
        vectors = embed_texts(
            texts,
            cfg.embed_base_url,
            cfg.embed_model,
            cfg.embed_api_key,
            on_progress=on_prog,
        )
        phases.append(
            PhaseStat(
                f"远程向量 {cfg.embed_model}  dim={vectors.shape[1]}",
                time.perf_counter() - t0,
                mon.rss_mb(),
            )
        )

        t0 = time.perf_counter()
        cos_idx, cos_sim = cosine_neighbors(vectors, k=k_use)
        phases.append(PhaseStat("精确余弦 Top50", time.perf_counter() - t0, mon.rss_mb()))

        pairs = [
            PairResult(i=i, j=j, cosine=s, bm25_norm=0.0)
            for (i, j), s in _topk_score_map(n, cos_idx, cos_sim).items()
        ]
        result = RunResult(questions=questions, pairs=pairs, has_vectors=True)
    except EmbedError as exc:
        ok = False
        err = str(exc)
        result = RunResult(questions=[], pairs=[], has_vectors=False)
    except Exception as exc:
        ok = False
        err = f"{type(exc).__name__}: {exc}"
        result = RunResult(questions=[], pairs=[], has_vectors=False)

    mon.stop()
    return _to_report(
        "语义余弦", ok, err, result, planted, phases,
        time.perf_counter() - wall0, time.process_time() - cpu0, mon,
    )


def _to_report(
    name: str,
    ok: bool,
    err: str,
    result: RunResult,
    planted: list[dict],
    phases: list[PhaseStat],
    wall_s: float,
    cpu_s: float,
    mon: ResourceMonitor,
) -> PathReport:
    nq = len(result.questions) if result else 0
    npairs = len(result.pairs) if result else 0
    return PathReport(
        name=name,
        ok=ok,
        error=err,
        n_questions=nq,
        n_pairs=npairs,
        hits_075=_count_hits(result, 0.75) if ok else 0,
        hits_060=_count_hits(result, 0.60) if ok else 0,
        planted_total=len(planted),
        planted_hit_075=_planted_hits(result, planted, 0.75) if ok else {},
        planted_hit_060=_planted_hits(result, planted, 0.60) if ok else {},
        phases=phases,
        wall_s=wall_s,
        cpu_s=cpu_s,
        peak_rss_mb=mon.peak_mb,
        avg_cpu_pct=mon.avg_cpu,
    )


def _fmt_kind(d: dict) -> str:
    if not d:
        return "-"
    parts = []
    for k, v in d.items():
        if k == "all":
            continue
        parts.append(f"{k} {v['hit']}/{v['total']}")
    allv = d.get("all", {})
    extra = f"合计 {allv.get('hit', 0)}/{allv.get('total', 0)}"
    return extra + "（" + "，".join(parts) + "）"


def print_report(rep: PathReport) -> None:
    print()
    print("=" * 64)
    print(f"{rep.name}  {'成功' if rep.ok else '失败'}")
    if rep.error:
        print(f"  错误: {rep.error}")
    print(f"  题量={rep.n_questions}  候选对(Top50去重)={rep.n_pairs}")
    print(f"  墙钟 {rep.wall_s:.2f}s   进程CPU {rep.cpu_s:.2f}s   "
          f"峰值RSS {rep.peak_rss_mb:.1f} MB   平均CPU {rep.avg_cpu_pct:.0f}%")
    print("  分阶段:")
    for p in rep.phases:
        print(f"    - {p.name}: {p.seconds:.2f}s  (之后RSS {p.rss_mb_after:.1f} MB)")
    if rep.ok:
        print(f"  阈值0.75命中 {rep.hits_075} 对    阈值0.60命中 {rep.hits_060} 对")
        print(f"  埋点召回@0.75  {_fmt_kind(rep.planted_hit_075)}")
        print(f"  埋点召回@0.60  {_fmt_kind(rep.planted_hit_060)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-embed", action="store_true")
    args = parser.parse_args()

    xlsx = STRESS_DIR / "stress_1000.xlsx"
    meta_path = STRESS_DIR / "stress_1000_meta.json"
    if not xlsx.is_file():
        print("先运行 python data-validation/stress/build_stress_dataset.py", file=sys.stderr)
        return 2
    meta = _load_meta(meta_path)
    planted = meta["planted"]
    print(f"数据集 {xlsx.name}  n={meta['n']}  埋点对={len(planted)}")

    reports = []

    print("\n>>> 开始 BM25 托底路径")
    bm25 = run_bm25(xlsx, planted)
    print_report(bm25)
    reports.append(bm25)

    if not args.skip_embed:
        cfg = _load_embed_cfg()
        print(f"\n>>> 开始语义路径  model={cfg.embed_model}  url={cfg.embed_base_url}")
        embed = run_embed(xlsx, planted, cfg)
        print_report(embed)
        reports.append(embed)
    else:
        print("\n跳过向量路径（--skip-embed）")

    out = STRESS_DIR / "stress_1000_report.json"
    payload = []
    for r in reports:
        d = asdict(r)
        payload.append(d)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写 {out}")
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
