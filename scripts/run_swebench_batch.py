"""SWE-bench 批量评测编排脚本（2026-09）。

复用仓库实验工具 ``run_agent.py`` / ``grade.py`` 的 stdout 标记约定，对任务清单逐实例执行
baseline / gated 双组 Agent 运行，并用官方 swebench harness（Docker）评分，产出 CSV / JSON
汇总。只做编排；工作区（git base checkout）与 venv 由 ``--prepare-workdir`` / ``--venv-python``
或外部脚本准备（见 docs/tutorials/swe-bench-real.md 的批量运行手册）。

用法::

    python scripts/run_swebench_batch.py \
        --tasks tasks.json --dataset swebench_dev.json \
        --agent-python .pytest_tmp/venv312/Scripts/python.exe \
        --agent-script .pytest_tmp/bench_data/run_agent.py \
        --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
        --grade-script .pytest_tmp/bench_data/grade.py \
        --workdir-root .pytest_tmp/bench_data/wd \
        --venv-python .pytest_tmp/bench_data/pd_venv/Scripts/python.exe \
        --modes baseline,gated --max-turns 60 \
        --outdir .pytest_tmp/bench_data/results \
        --skip-existing

输出::

    <outdir>/results.csv      每实例每模式一行指标
    <outdir>/results.json     汇总（含聚合 resolve 率）

退出码: 0 全部完成；1 存在失败/未 resolve 不影响汇总。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENT_PY = REPO_ROOT / ".pytest_tmp" / "venv312" / "Scripts" / "python.exe"
DEFAULT_AGENT = REPO_ROOT / ".pytest_tmp" / "bench_data" / "run_agent.py"
DEFAULT_GRADE_PY = REPO_ROOT / ".pytest_tmp" / "sweb_venv" / "Scripts" / "python.exe"
DEFAULT_GRADE = REPO_ROOT / ".pytest_tmp" / "bench_data" / "grade.py"


def load_tasks(path: Path) -> list[dict]:
    if not path.exists():
        print(f"任务清单不存在：{path}\n提示：示例任务文件在 .pytest_tmp/bench_data/ 下，需带该前缀。",
              file=sys.stderr)
        raise SystemExit(2)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("instances", "data", "rows", "tasks"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise ValueError(f"{path}: 无法识别任务清单")


def load_venv_map(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def venv_for(venv_map: dict[str, str], venv_python: str, iid: str, repo: str) -> str:
    return venv_map.get(iid) or venv_map.get(repo) or venv_python


def run(cmd: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def parse_pb(proc_out: str) -> dict:
    """从 stdout 中解析 PB_* 标记（兼容 run_agent.py / grade.py）。"""
    meta: dict = {}
    for line in (proc_out or "").splitlines():
        if line.startswith("PB_"):
            key, _, value = line.partition("=")
            meta[key] = value
    return meta


def prepare_workdir(workdir: Path, repo: str, base_commit: str, timeout: int) -> None:
    """workdir 不存在时创建 git checkout（init + fetch --depth 1 + checkout base）。"""
    if workdir.exists() and (workdir / ".git").exists():
        return
    workdir.mkdir(parents=True, exist_ok=True)
    remote = f"https://github.com/{repo}.git"
    run(["git", "-C", str(workdir), "init"], timeout=timeout)
    run(["git", "-C", str(workdir), "remote", "add", "origin", remote], timeout=timeout)
    run(["git", "-C", str(workdir), "fetch", "--depth", "1", "origin", base_commit],
        timeout=timeout)
    run(["git", "-C", str(workdir), "checkout", "-q", "FETCH_HEAD"], timeout=timeout)


def short_id(iid: str) -> str:
    return iid.replace("/", "_").replace("__", "_")


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-bench baseline/gated 批量评测编排")
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--agent-python", type=Path, default=DEFAULT_AGENT_PY)
    ap.add_argument("--agent-script", type=Path, default=DEFAULT_AGENT)
    ap.add_argument("--grade-python", type=Path, default=DEFAULT_GRADE_PY)
    ap.add_argument("--grade-script", type=Path, default=DEFAULT_GRADE)
    ap.add_argument("--workdir-root", type=Path, default=REPO_ROOT / ".pytest_tmp" / "bench_data" / "wd")
    ap.add_argument("--venv-python", default="")
    ap.add_argument("--venv-map", type=Path, default=None)
    ap.add_argument("--modes", default="baseline,gated")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--agent-timeout", type=int, default=1200, help="单个 Agent 运行秒数上限")
    ap.add_argument("--grade-timeout", type=int, default=1800)
    ap.add_argument("--outdir", type=Path, default=REPO_ROOT / ".pytest_tmp" / "bench_data" / "results")
    ap.add_argument("--prepare-workdir", action="store_true", help="缺失时自动 git base checkout")
    ap.add_argument("--skip-existing", action="store_true", help="已有评分结果则跳过")
    ap.add_argument("--instance", help="只跑指定 instance_id（调试用）")
    ap.add_argument("--rerun", action="store_true", help="忽略 results.csv 已有记录，强制重跑")
    ap.add_argument("--no-preflight", action="store_true", help="跳过 venv / Docker 镜像预检")
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    if not args.dataset.exists():
        print(f"数据集不存在：{args.dataset}", file=sys.stderr)
        return 2
    venv_map = load_venv_map(args.venv_map)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    outdir = args.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    rows_out: list[dict] = []
    csv_path = outdir / "results.csv"
    write_header = not csv_path.exists()
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=[
        "instance_id", "repo", "mode", "resolved", "turns", "seconds",
        "diff_chars", "gate_intercepts", "gate_final_stage", "gate_completed", "note"])
    if write_header:
        writer.writeheader()

    def grade_done(iid: str, mode: str) -> bool:
        label = f"{short_id(iid)}_{mode}"
        rep_dir = REPO_ROOT / ".pytest_tmp" / "bench_data" / "eval_runs" / label / "report"
        return rep_dir.exists() and any(rep_dir.glob("*.json"))

    # results.csv 断点续跑：已记录的 (instance, mode) 不重复执行
    done: set[tuple[str, str]] = set()
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as prev:
            for prev_row in csv.DictReader(prev):
                if prev_row.get("instance_id") and prev_row.get("mode"):
                    done.add((prev_row["instance_id"], prev_row["mode"]))

    def docker_image_ready(image: str) -> bool | None:
        """镜像已就绪 True；缺镜像 False；docker 不可用返回 None（跳过该检查）。"""
        if not image:
            return None
        try:
            probe = subprocess.run(["docker", "image", "inspect", image],
                                   capture_output=True, text=True, timeout=30)
        except Exception:
            return None
        return probe.returncode == 0

    rc = 0
    try:
        for task in tasks:
            iid = str(task["instance_id"])
            if args.instance and iid != args.instance:
                continue
            repo = str(task["repo"])
            wd = args.workdir_root.resolve() / iid
            if args.prepare_workdir:
                prepare_workdir(wd, repo, str(task["base_commit"]), args.agent_timeout)
            for mode in modes:
                label = f"{short_id(iid)}_{mode}"
                recorded = (iid, mode) in done
                if args.skip_existing:
                    recorded = recorded or grade_done(iid, mode)
                if recorded and not args.rerun:
                    print(f"[skip] {iid} {mode}（已有记录）")
                    continue
                venv_py = venv_for(venv_map, args.venv_python, iid, repo)
                if not args.no_preflight:
                    if not venv_py or not Path(venv_py).exists():
                        print(f"[skip] {iid} {mode}: venv 缺失 {venv_py or '(未提供 --venv-python)'}",
                              file=sys.stderr)
                        continue
                    img_ready = docker_image_ready(str(task.get("image") or ""))
                    if img_ready is False:
                        print(f"[skip] {iid} {mode}: Docker 镜像缺失 "
                              f"{task.get('image')}", file=sys.stderr)
                        continue
                elif not venv_py:
                    print(f"[skip] {iid} {mode}: 未提供 --venv-python", file=sys.stderr)
                    continue
                row: dict = {"instance_id": iid, "repo": repo, "mode": mode,
                             "note": ""}
                print(f"[run ] {iid} {mode} label={label}", flush=True)
                try:
                    proc = run([
                        str(args.agent_python), str(args.agent_script),
                        "--instance", iid, "--workdir", str(wd),
                        "--venv", venv_py, "--mode", mode,
                        "--label", label,
                        "--outdir", str(REPO_ROOT / ".pytest_tmp" / "bench_data" / "agent_runs"),
                        "--max-turns", str(args.max_turns),
                    ], timeout=args.agent_timeout)
                except subprocess.TimeoutExpired:
                    row.update({"note": "agent_timeout", "resolved": "0"})
                    rows_out.append(row)
                    writer.writerow(row)
                    fh.flush()
                    continue
                if proc.returncode != 0:
                    row.update({"note": f"agent_exit_{proc.returncode}", "resolved": "0"})
                    rows_out.append(row)
                    writer.writerow(row)
                    fh.flush()
                    continue
                pb = parse_pb(proc.stdout)
                row.update({
                    "turns": pb.get("PB_TURNS", ""),
                    "seconds": "",
                    "diff_chars": pb.get("PB_DIFF_CHARS", ""),
                    "gate_intercepts": pb.get("PB_GATE_INTERCEPTS", ""),
                    "gate_final_stage": pb.get("PB_GATE_FINAL_STAGE", ""),
                    "gate_completed": pb.get("PB_GATE_COMPLETED", ""),
                })
                # stats.json 补充真实耗时
                stats_path = (REPO_ROOT / ".pytest_tmp" / "bench_data" / "agent_runs"
                              / label / "stats.json")
                if stats_path.exists():
                    try:
                        stats = json.loads(stats_path.read_text(encoding="utf-8"))
                        row["seconds"] = stats.get("seconds", "")
                        row["turns"] = row["turns"] or stats.get("turns", "")
                    except Exception:
                        pass
                patch = (REPO_ROOT / ".pytest_tmp" / "bench_data" / "agent_runs"
                         / label / "model_patch.diff")
                if not (patch.exists() and patch.stat().st_size > 0):
                    row.update({"note": row["note"] + " empty_patch", "resolved": "0"})
                    rows_out.append(row)
                    writer.writerow(row)
                    fh.flush()
                    continue
                try:
                    gproc = run([
                        str(args.grade_python), str(args.grade_script),
                        "--instance", iid, "--dataset", str(args.dataset),
                        "--patch-file", str(patch),
                        "--label", label,
                        "--out-dir", str(REPO_ROOT / ".pytest_tmp" / "bench_data" / "eval_runs"),
                        "--timeout", str(args.grade_timeout),
                    ], timeout=args.grade_timeout + 60)
                except subprocess.TimeoutExpired:
                    row.update({"note": row["note"] + " grade_timeout", "resolved": "0"})
                    rows_out.append(row)
                    writer.writerow(row)
                    fh.flush()
                    continue
                gpb = parse_pb(gproc.stdout)
                row["resolved"] = gpb.get("PB_RESOLVED", "0")
                if row["note"] == "" and gpb.get("PB_GRADE_SUMMARY"):
                    row["note"] = "graded"
                rows_out.append(row)
                writer.writerow(row)
                fh.flush()
    finally:
        fh.close()

    summary = {"rows": rows_out}
    if rows_out:
        by_mode: dict[str, list[int]] = {}
        for r in rows_out:
            by_mode.setdefault(r["mode"], []).append(1 if str(r.get("resolved")) == "1" else 0)
        summary["resolve_rate"] = {m: (sum(v) / len(v) if v else None)
                                   for m, v in by_mode.items()}
    (outdir / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("PB_BATCH_SUMMARY=" + json.dumps(summary.get("resolve_rate", {}), ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
