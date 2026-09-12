"""结果分析：把评测 CSV 汇总成可复算的统计与 Markdown 表格（2026-09）。

动机：报告里的数字必须能一条命令复算，且要能回答「门禁到底救了哪些实例」。
本脚本只读 CSV，不跑 Docker / Agent，因此可在任何机器上复现。

用法::

    python benchmarks/swebench/analyze_results.py \
        --results benchmarks/swebench/results/scale20_lite_container.csv
    python benchmarks/swebench/analyze_results.py --results a.csv b.csv --format md

口径：

- ``resolved`` 取官方 harness 判定（``1``/``0``）。
- ``resolved`` 比例带 **Wilson score interval**（95%，z=1.96）；n 小时用它比正态近似更稳。
- ``被门禁救回`` = baseline 未 resolved 而同实例 gated resolved；
  ``被门禁拖累`` = baseline resolved 而同实例 gated 未 resolved。两者都逐实例列出。
- ``gate_*`` 列仅 gated 组有值；``gate_completed`` = ``State.delivery_clean()``。
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

MODES = ("baseline", "gated")


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (returns low, high)."""
    if total <= 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1.0 + z * z / total
    center = phat + z * z / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return ((center - margin) / denom, (center + margin) / denom)


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(rows: list[dict[str, str]], path: Path) -> dict:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("mode", ""))].append(row)
    summary: dict = {"path": str(path), "rows": len(rows), "modes": {}}
    for mode in MODES:
        group = grouped.get(mode)
        if not group:
            continue
        resolved = sum(1 for r in group if r.get("resolved") == "1")
        low, high = wilson(resolved, len(group))
        seconds = [s for s in (_float(r.get("seconds")) for r in group) if s is not None]
        turns = [_int(r.get("turns")) for r in group if r.get("turns")]
        summary["modes"][mode] = {
            "n": len(group),
            "resolved": resolved,
            "rate": resolved / len(group),
            "wilson_low": low,
            "wilson_high": high,
            "intercepts": sum(_int(r.get("gate_intercepts")) for r in group),
            "stage6": sum(1 for r in group if r.get("gate_final_stage") == "6"),
            "delivery_clean": sum(1 for r in group if r.get("gate_completed") == "1"),
            "empty_patch": sum(1 for r in group if not (r.get("diff_chars") or "").strip() or _int(r.get("diff_chars")) <= 0),
            "turns_mean": round(statistics.mean(turns), 1) if turns else 0.0,
            "seconds_mean": round(statistics.mean(seconds)) if seconds else 0,
            "seconds_median": round(statistics.median(seconds)) if seconds else 0,
            "seconds_p90": round(sorted(seconds)[max(0, math.ceil(0.9 * len(seconds)) - 1)]) if seconds else 0,
            "repos": dict(Counter(r.get("repo", "") for r in group)),
        }
    paired: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        paired[str(row.get("instance_id", ""))][str(row.get("mode", ""))] = row
    rescued, regressed = [], []
    for instance_id, by_mode in paired.items():
        base, gated = by_mode.get("baseline"), by_mode.get("gated")
        if not base or not gated:
            continue
        if base.get("resolved") == "0" and gated.get("resolved") == "1":
            rescued.append(instance_id)
        if base.get("resolved") == "1" and gated.get("resolved") == "0":
            regressed.append(instance_id)
    summary["rescued_by_gate"] = sorted(rescued)
    summary["regressed_under_gate"] = sorted(regressed)
    return summary


def render_markdown(summary: dict) -> str:
    lines = [f"### {summary['path']}（{summary['rows']} 行）", ""]
    lines.append("| 组别 | n | resolved | rate | Wilson 95% | 拦截 | 阶段6 | 交付全绿 | 空补丁 | 平均轮数 | 平均/中位/P90 耗时(s) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for mode in MODES:
        s = summary["modes"].get(mode)
        if not s:
            continue
        lines.append(
            f"| {mode} | {s['n']} | {s['resolved']} | {s['rate'] * 100:.0f}% | "
            f"[{s['wilson_low'] * 100:.1f}%, {s['wilson_high'] * 100:.1f}%] | {s['intercepts']} | "
            f"{s['stage6']} | {s['delivery_clean']} | {s['empty_patch']} | {s['turns_mean']} | "
            f"{s['seconds_mean']}/{s['seconds_median']}/{s['seconds_p90']} |"
        )
    lines.append("")
    lines.append(f"- 被门禁救回（baseline 失败 → gated 成功，{len(summary['rescued_by_gate'])} 个）："
                 + (", ".join(f"`{i}`" for i in summary["rescued_by_gate"]) or "无"))
    lines.append(f"- 被门禁拖累（baseline 成功 → gated 失败，{len(summary['regressed_under_gate'])} 个）："
                 + (", ".join(f"`{i}`" for i in summary["regressed_under_gate"]) or "无"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇总 SWE-bench 双组评测结果（可复算）")
    parser.add_argument("--results", nargs="+", required=True, help="一个或多个 results CSV")
    parser.add_argument("--format", choices=("text", "md"), default="text")
    args = parser.parse_args(argv)

    exit_code = 0
    for raw in args.results:
        path = Path(raw)
        if not path.exists():
            print(f"[missing] {path}", file=sys.stderr)
            exit_code = 1
            continue
        summary = summarize(_load(path), path)
        if args.format == "md":
            print(render_markdown(summary))
        else:
            print(f"== {summary['path']} ({summary['rows']} rows)")
            for mode in MODES:
                s = summary["modes"].get(mode)
                if not s:
                    continue
                print(
                    f"  {mode:8s} n={s['n']:3d} resolved={s['resolved']:3d} ({s['rate'] * 100:.0f}%) "
                    f"wilson95=[{s['wilson_low'] * 100:.1f}%,{s['wilson_high'] * 100:.1f}%] "
                    f"intercepts={s['intercepts']} stage6={s['stage6']} clean={s['delivery_clean']} "
                    f"empty={s['empty_patch']} turns={s['turns_mean']} "
                    f"sec={s['seconds_mean']}/{s['seconds_median']}/{s['seconds_p90']}"
                )
            print(f"  rescued={summary['rescued_by_gate'] or 'none'} regressed={summary['regressed_under_gate'] or 'none'}")
        print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
