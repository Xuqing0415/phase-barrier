"""官方 harness 自检：用数据集里的 gold patch 打分，必须 resolved=1（2026-09）。

为何需要：harness 本身出错时会**静默**把所有 Agent 结果打成假阴性。本仓库实测过一次——
Windows 上 ``Path.write_text`` 把 ``eval.sh`` 写成 CRLF，容器里 ``set -e`` / ``conda activate``
全部解析失败，表现为「所有 PASS_TO_PASS 全挂」，看起来像 Agent 全错。跑 Agent 评测前先跑
gold 对照，能把这类问题挡在门外。

用法::

    python benchmarks/swebench/gold_check.py \
        --dataset .pytest_tmp/bench_data/verified/dataset.json \
        --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
        --out-dir .pytest_tmp/bench_data/eval_gold

退出码：全部 gold resolved=1 时为 0，否则 1。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("instances", "data", "rows", "tasks"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise SystemExit(f"{path}: 无法识别数据集结构")


def parse_pb(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("PB_"):
            key, _, value = line.partition("=")
            meta[key] = value
    return meta


def select_gold(rows: list[dict], instances: list[str], limit: int) -> list[dict]:
    """挑出含非空 gold patch 的实例行。"""
    wanted = list(instances)
    if wanted:
        by_id = {str(r.get("instance_id")): r for r in rows}
        picked = [by_id[i] for i in wanted if i in by_id]
    else:
        picked = [r for r in rows if str(r.get("patch") or "").strip()]
    if limit:
        picked = picked[:limit]
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="SWE-bench gold patch 自检")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--instances", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--grade-python", required=True, type=Path)
    parser.add_argument("--grade-script", type=Path,
                        default=Path("benchmarks/swebench/grade.py"))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    rows = select_gold(load_rows(args.dataset), args.instances, args.limit)
    if not rows:
        print("没有可用的 gold 实例（数据集缺少 patch 或 --instances 不匹配）", file=sys.stderr)
        return 1

    gold_dir = args.out_dir / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for row in rows:
        iid = str(row["instance_id"])
        patch_file = gold_dir / f"{iid}.diff"
        patch_file.write_text(str(row["patch"]), encoding="utf-8", newline="\n")
        cmd = [str(args.grade_python), str(args.grade_script),
               "--instance", iid, "--dataset", str(args.dataset),
               "--patch-file", str(patch_file), "--label", f"gold_{iid}",
               "--out-dir", str(args.out_dir), "--timeout", str(args.timeout)]
        print(f"[gold] {iid}", flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=args.timeout + 300)
        meta = parse_pb(proc.stdout or "")
        ok = meta.get("PB_RESOLVED") == "1"
        print(f"  resolved={meta.get('PB_RESOLVED')} {meta.get('PB_GRADE_SUMMARY', '')}")
        if not ok:
            failures.append(iid)
    if failures:
        print(f"GOLD_CHECK_FAILED={' '.join(failures)}", file=sys.stderr)
        return 1
    print(f"PB_GOLD_CHECK_OK={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
