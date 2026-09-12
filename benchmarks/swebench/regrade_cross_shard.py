"""跨分片复核：把已产出的补丁在另一分片上重新评分（2026-09）。

动机：SWE-bench 的 Lite / Verified 分片对同一份补丁可能给出相反判定（Verified 的
FAIL_TO_PASS / PASS_TO_PASS 更严格）。本脚本读取一次双组评测的 ``results.csv``，
把其中的补丁在目标分片上用官方 harness 重新打分，输出对照 CSV —— 不需要重跑 Agent。

用法::

    python benchmarks/swebench/regrade_cross_shard.py \
        --results benchmarks/swebench/results/scale20_lite_container.csv \
        --target-dataset .pytest_tmp/bench_data/verified/dataset.json \
        --agent-runs .pytest_tmp/bench_data/agent_runs \
        --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
        --grade-script benchmarks/swebench/grade.py \
        --out-dir .pytest_tmp/bench_data/eval_runs_verified \
        --out-csv benchmarks/swebench/results/verified_regrade.csv

输出 CSV 列：``instance_id, mode, source_resolved, target_resolved, target_note``。
仅对目标分片里存在、且补丁文件存在的行评分；其余计入 ``skipped``。
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

COLUMNS = ["instance_id", "mode", "source_resolved", "target_resolved", "target_note"]


def short_id(instance_id: str) -> str:
    return instance_id.replace("/", "_").replace("__", "_")


def load_ids(path: Path) -> set[str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else next(
        raw[key] for key in ("instances", "data", "rows", "tasks") if isinstance(raw.get(key), list)
    )
    return {str(row["instance_id"]) for row in rows}


def parse_pb(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("PB_"):
            key, _, value = line.partition("=")
            meta[key] = value
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="跨分片重新评分")
    parser.add_argument("--results", required=True, type=Path, help="源 results.csv")
    parser.add_argument("--target-dataset", required=True, type=Path, help="目标分片实例 JSON")
    parser.add_argument("--agent-runs", required=True, type=Path, help="Agent 产出根目录")
    parser.add_argument("--grade-python", required=True, type=Path)
    parser.add_argument("--grade-script", type=Path,
                        default=Path("benchmarks/swebench/grade.py"))
    parser.add_argument("--out-dir", required=True, type=Path, help="评分日志/报告目录")
    parser.add_argument("--out-csv", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--label-suffix", default="",
                        help="与 run_swebench_batch_container.py 的 --label-suffix 一致")
    args = parser.parse_args()

    target_ids = load_ids(args.target_dataset)
    with open(args.results, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("instance_id")]

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.out_csv.exists() or args.out_csv.stat().st_size == 0
    skipped = 0
    with open(args.out_csv, "a", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        if fresh:
            writer.writeheader()
        for row in rows:
            iid, mode = row["instance_id"], row["mode"]
            if iid not in target_ids:
                skipped += 1
                continue
            label = f"{short_id(iid)}_{mode}{args.label_suffix}"
            patch = args.agent_runs / label / "model_patch.diff"
            if not patch.exists() or patch.stat().st_size == 0:
                skipped += 1
                continue
            target_label = f"{label}__verified"
            cmd = [str(args.grade_python), str(args.grade_script),
                   "--instance", iid, "--dataset", str(args.target_dataset),
                   "--patch-file", str(patch), "--label", target_label,
                   "--out-dir", str(args.out_dir), "--timeout", str(args.timeout)]
            print(f"[regrade] {iid} {mode}", flush=True)
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=args.timeout + 300)
            meta = parse_pb(proc.stdout or "")
            writer.writerow({
                "instance_id": iid, "mode": mode,
                "source_resolved": row.get("resolved", ""),
                "target_resolved": meta.get("PB_RESOLVED", "0"),
                "target_note": meta.get("PB_GRADE_SUMMARY", "")[:200],
            })
            out.flush()
    print(f"PB_REGRADE_DONE=1 skipped={skipped} out={args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
