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
缺镜像、缺 patch、超时按行记录 note；模型调用/评分基础设施故障按根因策略停止推进。
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Load the sibling by path: launchers may import this module from another cwd,
# where a third-party package named "scripts" would otherwise shadow the repo.
_checks_spec = importlib.util.spec_from_file_location(
    "pb_verified20_checks", Path(__file__).with_name("verified20_checks.py"))
_checks = importlib.util.module_from_spec(_checks_spec)
_checks_spec.loader.exec_module(_checks)
model_call_failed = _checks.model_call_failed
monitoring_decision = _checks.monitoring_decision
MOUNT_POINT = "/pb"
# 官方评测镜像的 base conda 里没有 phase-barrier 的运行时依赖（pydantic/PyYAML/structlog），
# 而镜像自带的 testbed 环境又不满足 requires-python>=3.10。用命名卷缓存一份按 Python 小版本
# 分目录的依赖，首次运行 pip 安装、后续复用；并发时用 mkdir 锁避免写坏同一目录。
GATE_DEPS_MOUNT = "/pb_gate_deps"
GATE_DEPS_VOLUME = "pb_gate_deps"
GATE_DEPS_PACKAGES = "pydantic PyYAML structlog"

#: 只给 Agent 的字段。gold patch / test_patch / FAIL_TO_PASS 等评分信息绝不能进容器。
AGENT_DATASET_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")


def gate_deps_bootstrap(gate_python: str) -> str:
    """返回容器内前置 shell 片段：按门禁解释器的小版本缓存并安装门禁依赖。

    镜像里 PATH 上的 ``python`` 往往是 testbed 环境（可能是 3.6/3.9），不是跑门禁的
    base conda——必须用 ``gate_python`` 判定依赖目录，否则会装出 ABI 不匹配的包。

    v0.62.0：安装前先清空目录、装完用 ``import pydantic, yaml, structlog`` 校验，
    成功才落 ``.ready``。Docker Desktop 的卷挂载下 ``mkdir`` 锁不保证互斥，两个容器
    并发 pip install 会把目录写坏（实测出现过 pydantic 装上了、``typing_extensions``
    缺失，之后同卷容器全部 import 失败）。

    v1.0.0：改为「共享卷只读复制 + 容器内私有目录 + 校验后原子发布」。
    实测复现：``mkdir`` 锁在并发下真的会同时进临界区，两个容器各自 ``rm -rf`` +
    pip install，先完成的那个 ``touch .ready`` 之后仍被另一个容器的写入破坏，
    随后所有容器 ``import typing_extensions`` 失败（2026-09-12 批量跑 Scale-30 时
    4 个 gated 任务因此空补丁）。现在：

    1. 使用方只把共享缓存 ``cp -a`` 到容器私有 ``$LOCAL``，再校验 import；
       共享卷上任何并发写都不会影响正在运行的容器。
    2. 缓存缺失或校验不过时，在 ``$LOCAL`` 里重装（不碰共享卷）。
    3. 发布缓存走唯一临时目录 + ``mv``，且**不覆盖已有缓存**；不再有 ``rm -rf``
       共享目录的写前清空，坏缓存只在自身校验失败时清除（可自愈）。
    """
    return (
        'DEPS_BASE="' + GATE_DEPS_MOUNT + '/site-$(' + gate_python
        + ' -c \'import sys;print("%d.%d"%sys.version_info[:2])\')"; '
        'mkdir -p ' + GATE_DEPS_MOUNT + '; '
        'LOCAL="/tmp/pb_gate_deps_$$"; mkdir -p "$LOCAL"; '
        'py_ok() { PYTHONPATH="$1" ' + gate_python + ' -c "import pydantic, yaml, structlog" 2>/dev/null; }; '
        'if [ -f "$DEPS_BASE/.ready" ] && py_ok "$DEPS_BASE"; then '
        'cp -a "$DEPS_BASE/." "$LOCAL/" 2>/dev/null; '
        'elif [ -e "$DEPS_BASE" ]; then rm -rf "$DEPS_BASE" 2>/dev/null; fi; '
        'if ! py_ok "$LOCAL"; then '
        'rm -rf "$LOCAL"; mkdir -p "$LOCAL"; '
        + gate_python + ' -m pip install -q --target "$LOCAL" ' + GATE_DEPS_PACKAGES
        + ' && py_ok "$LOCAL" || { echo "pb_gate_deps: 门禁依赖安装失败" >&2; exit 1; }; '
        'TMP="$DEPS_BASE.pub.$$"; rm -rf "$TMP"; mkdir -p "$TMP"; '
        'cp -a "$LOCAL/." "$TMP/" 2>/dev/null && touch "$TMP/.ready" && '
        '{ [ -e "$DEPS_BASE" ] || mv "$TMP" "$DEPS_BASE" 2>/dev/null; }; '
        'rm -rf "$TMP" 2>/dev/null; fi; '
        'export PYTHONPATH="$LOCAL:${PYTHONPATH:-}"; '
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


def grade_note_from_pb(meta: dict[str, str]) -> str:
    """从评分标记中取审计 note；旧版 grade.py 没有 PB_GRADE_NOTE 时回退 graded。"""
    note = (meta.get("PB_GRADE_NOTE") or "").strip()
    if note:
        return note
    return "graded" if meta.get("PB_GRADE_SUMMARY") else ""


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


def build_agent_docker_command(*, image: str, repo_mount: Path | str | None = None,
                               mounts: list[tuple[Path | str, str, bool]] | None = None,
                               agent_script: str, dataset: str, instance_id: str,
                               mode: str, label: str, outdir: str,
                               testbed_python: str, gate_python: str,
                               max_turns: int, exec_timeout: int = 300,
                               mount_point: str = MOUNT_POINT,
                               extra_env: dict[str, str] | None = None,
                               docker: str = "docker",
                               dns_servers: list[str] | None = None) -> list[str]:
    """构造在官方镜像里运行 run_agent.py 的 ``docker run`` argv（纯函数，便于测试）。

    ``mounts`` 为 ``(宿主路径, 容器路径, 只读)``。正式评测传入最小挂载集：
    只读的 agent 脚本 / anti_shortcut / 脱敏数据集 + 可写的 runs 目录；
    不挂载整个仓库，避免 Agent 读到 gold patch。
    """
    if mounts is None:
        if repo_mount is None:
            raise ValueError("build_agent_docker_command 需要 repo_mount 或 mounts")
        mounts = [(repo_mount, mount_point, False)]
    script = (
        gate_deps_bootstrap(gate_python)
        + f"{gate_python} {agent_script}"
        f" --dataset {dataset} --instance {instance_id} --workdir /testbed"
        f" --venv {testbed_python} --mode {mode} --label {label}"
        f" --outdir {outdir} --max-turns {max_turns} --exec-timeout {exec_timeout}"
    )
    argv = [docker, "run", "--rm"]
    for server in dns_servers or []:
        argv += ["--dns", server]
    for host_path, container_path_, readonly in mounts:
        spec = f"{host_path}:{container_path_}" + (":ro" if readonly else "")
        argv += ["-v", spec]
    argv += ["-v", f"{GATE_DEPS_VOLUME}:{GATE_DEPS_MOUNT}", "-w", "/testbed"]
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


def local_images(docker: str = "docker") -> set[str]:
    """本机已缓存的镜像集合（``--only-ready`` 用）。"""
    proc = subprocess.run([docker, "images", "--format", "{{.Repository}}:{{.Tag}}"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"docker images 失败，--only-ready 无法判定：{proc.stderr.strip()}")
    return {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}


def filter_ready(tasks: list[dict], images: set[str]) -> list[dict]:
    """只保留镜像已缓存的实例（扩样本时可边拉边跑，不必等全部拉完）。"""
    ready: list[dict] = []
    for task in tasks:
        image = container_image(task)
        if not image.endswith(":latest"):
            image += ":latest"
        if image in images:
            ready.append(task)
    return ready


def _run(cmd: list[str], timeout: int, log_path: Path | None = None):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if log_path is not None:
        # Keep partial output even if the supervisor/CLI disappears. Capturing
        # everything in RAM and writing only after completion loses the most
        # useful evidence on interruption.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as log:
            proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        return proc, log_path.read_text(encoding="utf-8", errors="replace")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc, combined


def build_agent_mounts(*, agent_script: Path | str, repo_root: Path | str,
                       agent_dataset: Path | str,
                       agent_runs: Path | str) -> list[tuple[Path, str, bool]]:
    """正式评测的最小挂载集：脚本 / 门禁包 / 脱敏数据集只读，runs 目录可写。

    绝不挂载宿主仓库根，否则 Agent 可通过 shell 读到 ``dataset.json`` 里的
    gold patch / test_patch。
    """
    return [
        (Path(agent_script).resolve(),
         "/pb/benchmarks/swebench/run_agent.py", True),
        (Path(repo_root).resolve() / "anti_shortcut", "/pb/anti_shortcut", True),
        (Path(agent_dataset).resolve(), "/pb/dataset_agent.json", True),
        (Path(agent_runs).resolve(), "/pb_runs", False),
    ]


def write_run_status(path: Path, *, state: str, **fields) -> None:
    """写 status.json；``runner_pid`` 供 run_status.load_status 判断僵尸运行。"""
    payload = {
        "state": state,
        "runner_pid": os.getpid(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **fields,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
    agent_mounts = build_agent_mounts(
        agent_script=args.agent_script,
        repo_root=args.repo_mount,
        agent_dataset=args.agent_dataset,
        agent_runs=args.agent_runs,
    )
    argv = build_agent_docker_command(
        image=container_image(instance), mounts=agent_mounts,
        agent_script="/pb/benchmarks/swebench/run_agent.py",
        dataset="/pb/dataset_agent.json",
        instance_id=iid, mode=mode, label=label,
        outdir="/pb_runs",
        testbed_python=args.testbed_python, gate_python=args.gate_python,
        max_turns=args.max_turns, exec_timeout=args.exec_timeout,
        dns_servers=getattr(args, "dns", None),
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
    if model_call_failed(out):
        row["note"] = "model_call_error" + (" empty_patch" if not patch_is_fresh(patch, started_at) else "")
        # A transport failure is not an official unresolved result. Preserve any
        # saved patch so the recovery plan can schedule grading without a model rerun.
        row["resolved"] = ""
        return row
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
    if row["note"] == "":
        note = grade_note_from_pb(gpb)
        if note:
            row["note"] = note
    if gproc.returncode not in (0, 2):  # grade.py: 0=resolved, 2=unresolved
        row["note"] = (row["note"] + f" grade_exit_{gproc.returncode}").strip()
    return row


def write_agent_dataset(dataset: Path, instance_ids: set[str], out: Path) -> Path:
    """写出仅含 Agent 必需字段的数据集（不含 gold patch / test_patch / 测试名）。"""
    safe_rows: list[dict] = []
    for row in _load_rows(dataset):
        if str(row.get("instance_id")) not in instance_ids:
            continue
        safe_rows.append({key: row.get(key, "") for key in AGENT_DATASET_FIELDS})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(safe_rows, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return out


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
    ap.add_argument("--only-ready", action="store_true",
                    help="只跑本机已缓存镜像的实例（扩样本时边拉边跑；配合 --skip-existing 可续跑）")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--exec-timeout", type=int, default=300)
    ap.add_argument("--agent-timeout", type=int, default=5400)
    ap.add_argument("--grade-timeout", type=int, default=1800)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--label-suffix", default="")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--dns", action="append", default=[],
                    help="DNS server for Agent containers; repeat for a fallback server")
    args = ap.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    instances = {str(r["instance_id"]): r for r in _load_rows(args.dataset)}
    tasks = [t for t in _load_rows(args.tasks) if str(t.get("instance_id")) in instances]
    if args.only_ready:
        before = len(tasks)
        tasks = filter_ready(tasks, local_images())
        print(f"--only-ready：{len(tasks)}/{before} 个实例的镜像已缓存", flush=True)

    args.results.parent.mkdir(parents=True, exist_ok=True)
    Path(args.agent_runs).mkdir(parents=True, exist_ok=True)
    args.agent_dataset = write_agent_dataset(
        args.dataset,
        {str(t["instance_id"]) for t in tasks},
        args.results.parent / "_agent_dataset.json",
    )

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

    status_path = args.results.parent / "status.json"
    write_run_status(
        status_path,
        state="running",
        results=str(args.results),
        tasks=len(tasks),
        jobs=len(jobs),
        modes=modes,
        concurrency=args.concurrency,
    )

    args.results.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.results.exists() or args.results.stat().st_size == 0
    fh = open(args.results, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=COLUMNS)
    if fresh:
        writer.writeheader()
        fh.flush()
    threads: list[threading.Thread] = []
    stop_event = threading.Event()
    new_rows: list[dict] = []

    def worker(job: dict) -> None:
        try:
            row = run_one(job, args, instances)
        except Exception as exc:  # 单点失败不拖垮整批
            row = {**job, "resolved": "", "note": f"driver_error:{exc}"}
        with _LOCK:
            writer.writerow(row)
            fh.flush()
            new_rows.append(row)
            if monitoring_decision(new_rows, args.log_dir or "", args.label_suffix)["stop"]:
                stop_event.set()

    for job in jobs:
        while (not stop_event.is_set()
               and sum(1 for t in threads if t.is_alive()) >= max(1, args.concurrency)):
            time.sleep(5)
        if stop_event.is_set():
            break
        thread = threading.Thread(target=worker, args=(job,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    fh.close()
    write_run_status(
        status_path,
        state="halted" if stop_event.is_set() else "completed",
        results=str(args.results),
        tasks=len(tasks),
        jobs=len(jobs),
        modes=modes,
        concurrency=args.concurrency,
    )
    print("PB_CONTAINER_BATCH_DONE=" + str(len(threads)), flush=True)
    return 3 if stop_event.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
