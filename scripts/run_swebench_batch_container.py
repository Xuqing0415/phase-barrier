"""SWE-bench 容器内双组评测编排（官方 eval 镜像里跑 Agent，2026-09）。

与 ``run_swebench_batch.py`` 的差别：Agent 不在宿主 venv 里跑，而是在每个实例的官方
``swebench/sweb.eval.x86_64.*`` 镜像容器内运行——容器 ``/testbed`` 就是该实例
``base_commit`` 的仓库、依赖已在镜像的 testbed conda 环境里装好。这样可避免宿主
Python 版本与老仓库依赖不匹配（例如宿主 py3.14 跑不动 astropy/sklearn 老版本，
镜像里是 py3.6-3.11 的对应环境）。

门禁与测试解释器的分工：

- phase-barrier 要求 ``requires-python >= 3.10``，而不少老任务镜像的 testbed 环境是
  py3.6/3.8/3.9。镜像的 base conda python 通常是 3.11，因此 **门禁用 base conda
  python 执行**（``run_agent.py`` 本身），**测试命令经 PATH 指向 testbed 解释器**
  （``--venv`` 参数），两者互不影响。
- 宿主仍需 ``grade.py``（swebench harness + Docker）对生成的补丁打分。

环境：宿主需 docker CLI 与已拉取的评测镜像；代理脚本在仓库内（``benchmarks/swebench/``），默认即指向同仓库路径。

用法（宿主机执行）::

    python scripts/run_swebench_batch_container.py \
        --tasks .pytest_tmp/bench_data/lite/tasks_scale20.json \
        --dataset .pytest_tmp/bench_data/lite/dataset.json \
        --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
        --results .pytest_tmp/bench_data/results_scale20/results.csv \
        --log-dir .pytest_tmp/bench_data/container_logs \
        --modes baseline,gated --max-turns 60 --concurrency 2 --skip-existing

``--agent-script`` / ``--grade-script`` 默认已指向仓库内的
``benchmarks/swebench/{run_agent,grade}.py``，无需显式传入。

输出：``--results`` CSV 追加 ``(instance_id, mode)`` 行；已存在的组合自动跳过，可断点续跑。
缺镜像、缺 patch、超时都会按行记录 note，不中断整批。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOUNT_POINT = "/pb"
# 官方评测镜像的 base conda 里没有 phase-barrier 的运行时依赖（pydantic/PyYAML/structlog），
# 而镜像自带的 testbed 环境又不满足 requires-python>=3.10。用命名卷缓存一份按 Python 小版本
# 分目录的依赖，首次运行 pip 安装、后续复用；并发时用 mkdir 锁避免写坏同一目录。
GATE_DEPS_MOUNT = "/pb_gate_deps"
GATE_DEPS_VOLUME = "pb_gate_deps"
GATE_DEPS_PACKAGES = "pydantic PyYAML structlog"


def gate_deps_bootstrap(gate_python: str) -> str:
    """返回容器内前置 shell 片段：按门禁解释器的小版本缓存并安装门禁依赖。

    镜像里 PATH 上的 ``python`` 往往是 testbed 环境（可能是 3.6/3.9），不是跑门禁的
    base conda——必须用 ``gate_python`` 判定依赖目录，否则会装出 ABI 不匹配的包。

    v0.62.0：安装前先清空目录、装完用 ``import pydantic, yaml, structlog`` 校验，
    成功才落 ``.ready``。Docker Desktop 的卷挂载下 ``mkdir`` 锁不保证互斥，两个容器
    并发 pip install 会把目录写坏（实测出现过 pydantic 装上了、``typing_extensions``
    缺失，之后同卷容器全部 import 失败）。
    """
    return (
        'DEPS="' + GATE_DEPS_MOUNT + '/site-$(' + gate_python
        + ' -c \'import sys;print("%d.%d"%sys.version_info[:2])\')"; '
        'mkdir -p ' + GATE_DEPS_MOUNT + '; '
        'if [ ! -f "$DEPS/.ready" ]; then '
        'if mkdir "$DEPS.lock" 2>/dev/null; then '
        'rm -rf "$DEPS"; mkdir -p "$DEPS"; '
        + gate_python + ' -m pip install -q --target "$DEPS" ' + GATE_DEPS_PACKAGES
        + ' && PYTHONPATH="$DEPS" ' + gate_python
        + ' -c "import pydantic, yaml, structlog" && touch "$DEPS/.ready"; '
        'rmdir "$DEPS.lock" 2>/dev/null; '
        'else for _i in $(seq 1 90); do [ -f "$DEPS/.ready" ] && break; sleep 5; done; fi; fi; '
        'export PYTHONPATH="$DEPS:${PYTHONPATH:-}"; '
    )
COLUMNS = ["instance_id", "repo", "mode", "resolved", "turns", "seconds",
           "diff_chars", "gate_intercepts", "gate_final_stage", "gate_completed", "note"]
_LOCK = threading.Lock()


def short_id(instance_id: str) -> str:
    """实例 id -> 文件系统安全的标签前缀。"""
    return instance_id.replace("/", "_").replace("__", "_")


def parse_pb(text: str) -> dict[str, str]:
    """解析 run_agent.py / grade.py 打印的 ``PB_*`` 标记。"""
    meta: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("PB_"):
            key, _, value = line.partition("=")
            meta[key] = value
    return meta


def container_image(instance: dict) -> str:
    """实例对应的官方评测镜像：优先取数据集 ``image`` 列，否则按命名规则推导。"""
    image = str(instance.get("image") or "").strip()
    if image:
        return image
    return ("swebench/sweb.eval.x86_64."
            + str(instance["instance_id"]).replace("__", "_1776_") + ":latest")


def container_path(host_path: Path | str, repo_mount: Path | str,
                   mount_point: str = MOUNT_POINT) -> str:
    """把宿主路径翻译成容器内路径（要求位于 --repo-mount 之下）。"""
    rel = Path(host_path).resolve().relative_to(Path(repo_mount).resolve())
    return f"{mount_point}/{rel.as_posix()}"


def patch_is_fresh(patch: Path, since: float) -> bool:
    """补丁文件是否存在、非空，且确实是本次运行（不早于 ``since``）产生的。

    只判「文件存在且非空」会把上一轮同名运行的旧补丁当成本次结果去评分：
    Agent 在容器里 import 失败秒退时，旧补丁仍在挂载目录里（实测踩坑）。
    """
    try:
        stat = Path(patch).stat()
    except OSError:
        return False
    return stat.st_size > 0 and stat.st_mtime >= since


def build_agent_docker_command(*, image: str, repo_mount: Path | str, agent_script: str,
                               dataset: str, instance_id: str, mode: str, label: str,
                               outdir: str, testbed_python: str, gate_python: str,
                               max_turns: int, exec_timeout: int = 300,
                               mount_point: str = MOUNT_POINT,
                               extra_env: dict[str, str] | None = None,
                               docker: str = "docker") -> list[str]:
    """构造在官方镜像里运行 run_agent.py 的 ``docker run`` argv（纯函数，便于测试）。"""
    script = (
        gate_deps_bootstrap(gate_python)
        + f"{gate_python} {agent_script}"
        f" --dataset {dataset} --instance {instance_id} --workdir /testbed"
        f" --venv {testbed_python} --mode {mode} --label {label}"
        f" --outdir {outdir} --max-turns {max_turns} --exec-timeout {exec_timeout}"
    )
    argv = [docker, "run", "--rm",
            "-v", f"{repo_mount}:{mount_point}",
            "-v", f"{GATE_DEPS_VOLUME}:{GATE_DEPS_MOUNT}",
            "-w", "/testbed"]
    for key, value in (extra_env or {}).items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--entrypoint", "/bin/bash", image, "-lc", script]
    return argv


def pending_jobs(tasks: list[dict], done: set[tuple[str, str]], modes: list[str]) -> list[dict]:
    """按 (instance, mode) 过滤出未记录的待跑任务（保持清单顺序）。"""
    jobs: list[dict] = []
    for task in tasks:
        iid = str(task["instance_id"])
        for mode in modes:
            if (iid, mode) not in done:
                jobs.append({"instance_id": iid, "repo": str(task.get("repo", "")), "mode": mode})
    return jobs


def _run(cmd: list[str], timeout: int, log_path: Path | None = None):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(combined, encoding="utf-8", errors="replace")
    return proc, combined


def run_one(job: dict, args, instances: dict[str, dict]) -> dict:
    iid, mode = job["instance_id"], job["mode"]
    label = f"{short_id(iid)}_{mode}{args.label_suffix}"
    instance = instances[iid]
    log = Path(args.log_dir) / f"{label}.container.log" if args.log_dir else None
    label_dir = Path(args.agent_runs) / label
    # 清掉上一轮同名运行的 patch / stats，避免 Agent 未产出时拿旧补丁评分
    if label_dir.exists():
        shutil.rmtree(label_dir, ignore_errors=True)
    started_at = time.time()
    argv = build_agent_docker_command(
        image=container_image(instance), repo_mount=args.repo_mount,
        agent_script=container_path(args.agent_script, args.repo_mount, args.mount_point),
        dataset=container_path(args.dataset, args.repo_mount, args.mount_point),
        instance_id=iid, mode=mode, label=label,
        outdir=container_path(args.agent_runs, args.repo_mount, args.mount_point),
        testbed_python=args.testbed_python, gate_python=args.gate_python,
        max_turns=args.max_turns, exec_timeout=args.exec_timeout,
        mount_point=args.mount_point,
        extra_env={"DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
                   **({"PB_DS_MODEL": os.environ["PB_DS_MODEL"]} if os.environ.get("PB_DS_MODEL") else {})},
    )
    print(f"[{time.strftime('%H:%M:%S')}] [run ] {iid} {mode} {container_image(instance)}", flush=True)
    try:
        proc, out = _run(argv, args.agent_timeout, log)
    except subprocess.TimeoutExpired:
        return {**job, "resolved": "", "note": "container_timeout"}
    pb = parse_pb(out)
    patch = label_dir / "model_patch.diff"
    row = {"instance_id": iid, "repo": job["repo"], "mode": mode, "resolved": "",
           "turns": pb.get("PB_TURNS", ""), "seconds": "",
           "diff_chars": pb.get("PB_DIFF_CHARS", ""),
           "gate_intercepts": pb.get("PB_GATE_INTERCEPTS", ""),
           "gate_final_stage": pb.get("PB_GATE_FINAL_STAGE", ""),
           "gate_completed": pb.get("PB_GATE_COMPLETED", ""),
           "note": "" if proc.returncode == 0 else f"agent_exit_{proc.returncode}"}
    stats_path = Path(args.agent_runs) / label / "stats.json"
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            row["turns"] = stats.get("turns", row["turns"])
            row["seconds"] = stats.get("seconds", "")
        except Exception:
            pass
    if not patch_is_fresh(patch, started_at):
        row["note"] = (row["note"] + " empty_patch").strip()
        row["resolved"] = "0"
        return row
    print(f"[{time.strftime('%H:%M:%S')}] [grade] {iid} {mode}", flush=True)
    grade_cmd = [str(args.grade_python), str(args.grade_script), "--instance", iid,
                 "--dataset", str(args.dataset), "--patch-file", str(patch),
                 "--label", label, "--out-dir", str(args.eval_runs),
                 "--timeout", str(args.grade_timeout)]
    try:
        gproc, gout = _run(grade_cmd, args.grade_timeout + 120,
                           Path(args.log_dir) / f"{label}.grade.log" if args.log_dir else None)
    except subprocess.TimeoutExpired:
        row["note"] = (row["note"] + " grade_timeout").strip()
        row["resolved"] = "0"
        return row
    gpb = parse_pb(gout)
    row["resolved"] = gpb.get("PB_RESOLVED", "0")
    if row["note"] == "" and "PB_GRADE_SUMMARY" in gpb:
        row["note"] = "graded"
    if gproc.returncode not in (0, 2):  # grade.py: 0=resolved, 2=unresolved
        row["note"] = (row["note"] + f" grade_exit_{gproc.returncode}").strip()
    return row


def _load_rows(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("tasks", "instances", "data", "rows"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise ValueError(f"{path}: 无法识别清单结构")


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-bench 官方镜像内双组评测编排")
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--agent-script", type=Path,
                    default=REPO_ROOT / "benchmarks" / "swebench" / "run_agent.py")
    ap.add_argument("--grade-script", type=Path,
                    default=REPO_ROOT / "benchmarks" / "swebench" / "grade.py")
    ap.add_argument("--grade-python", type=Path,
                    default=REPO_ROOT / ".pytest_tmp" / "sweb_venv" / "Scripts" / "python.exe")
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--agent-runs", type=Path,
                    default=REPO_ROOT / ".pytest_tmp" / "bench_data" / "agent_runs")
    ap.add_argument("--eval-runs", type=Path,
                    default=REPO_ROOT / ".pytest_tmp" / "bench_data" / "eval_runs")
    ap.add_argument("--log-dir", type=Path, default=None, help="容器/评分日志目录（可选）")
    ap.add_argument("--repo-mount", type=Path, default=REPO_ROOT, help="挂载进容器的宿主仓库根")
    ap.add_argument("--mount-point", default=MOUNT_POINT, help="容器内挂载点（默认 /pb）")
    ap.add_argument("--gate-python", default="/opt/miniconda3/bin/python", help="容器内跑门禁的解释器")
    ap.add_argument("--testbed-python", default="/opt/miniconda3/envs/testbed/bin/python",
                    help="容器内跑测试/布局命令的解释器")
    ap.add_argument("--modes", default="baseline,gated")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--exec-timeout", type=int, default=300)
    ap.add_argument("--agent-timeout", type=int, default=5400)
    ap.add_argument("--grade-timeout", type=int, default=1800)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--label-suffix", default="")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    instances = {str(r["instance_id"]): r for r in _load_rows(args.dataset)}
    tasks = [t for t in _load_rows(args.tasks) if str(t.get("instance_id")) in instances]

    done: set[tuple[str, str]] = set()
    if args.results.exists():
        with open(args.results, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("instance_id") and r.get("mode"):
                    done.add((r["instance_id"], r["mode"]))
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if args.skip_existing:
        jobs = pending_jobs(tasks, done, modes)
    else:
        jobs = [{"instance_id": str(t["instance_id"]), "repo": str(t.get("repo", "")), "mode": m}
                for t in tasks for m in modes]
    print(f"待跑 {len(jobs)} 个 (instance, mode)", flush=True)

    args.results.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.results.exists() or args.results.stat().st_size == 0
    fh = open(args.results, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=COLUMNS)
    if fresh:
        writer.writeheader()
        fh.flush()
    threads: list[threading.Thread] = []

    def worker(job: dict) -> None:
        try:
            row = run_one(job, args, instances)
        except Exception as exc:  # 单点失败不拖垮整批
            row = {**job, "resolved": "", "note": f"driver_error:{exc}"}
        with _LOCK:
            writer.writerow(row)
            fh.flush()

    for job in jobs:
        while sum(1 for t in threads if t.is_alive()) >= max(1, args.concurrency):
            time.sleep(5)
        thread = threading.Thread(target=worker, args=(job,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    fh.close()
    print("PB_CONTAINER_BATCH_DONE=" + str(len(jobs)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
