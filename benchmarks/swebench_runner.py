"""真实 SWE-bench 评测 harness（v0.41.0）：在官方实例集上编排“基线 vs 门禁”双组 Agent 评测。

定位：本模块是**编排与统计层**，负责加载实例清单、为每个实例准备独立工作目录、按命令模板
分别运行基线 / 门禁 Agent、解析结果标记并聚合指标；不做官方 swebench harness 的容器与
隐藏测试打分（真实运行仍需用户环境，见 ``docs/tutorials/swe-bench-real.md``）。

实例清单与官方 SWE-bench 数据集同构：每一项含 ``instance_id`` / ``repo`` /
``base_commit`` / ``problem_statement`` / ``patch`` / ``test_patch``（评测时可只保留
harness 需要的字段，其余透传）。

命令模板占位符：``{id}`` = 实例 id，``{workdir}`` = 实例工作目录（harness 预先创建）。
Agent 包装脚本 / 隐藏测试执行器按以下 stdout 标记回报结果：

- ``PB_RESOLVED=1|0``：该实例最终是否 resolve（官方 harness 跑隐藏测试后写入）
- ``PB_GATE_INTERCEPTS=N``：门禁拦截次数（无门禁基线恒为 0）
- 未写任何标记时按退出码推断：0 视为 resolve

聚合指标：baseline / gated 各自的 resolve 率、平均耗时与总拦截率；``--fail-fast`` 按阈值
判定（默认 gated resolve 率不得低于 baseline，且 gated 拦截率必须 > 0 才能证明门禁生效）。

用法::

    # 冒烟（合成实例，确定性，无需 SWE-bench / Docker）
    python benchmarks/swebench_runner.py --synthetic 20 --fail-fast

    # 真实评测（用户提供命令模板与已就绪的 checkout / 容器环境）
    python benchmarks/swebench_runner.py --instances swebench_instances.json \
        --cmd-baseline 'bash run_agent.sh {id} {workdir} no-gate' \
        --cmd-gated 'bash run_agent.sh {id} {workdir} with-gate' \
        --json
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_INSTANCE_FIELDS = ("instance_id",)


def _load_instances(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        instances = data.get("instances") or data.get("data") or []
    elif isinstance(data, list):
        instances = data
    else:
        raise ValueError("实例清单必须是 JSON 数组或含 instances/data 字段的对象")
    out = []
    for item in instances:
        if not isinstance(item, dict) or not item.get("instance_id"):
            continue
        out.append(item)
    if not out:
        raise ValueError(f"实例清单为空或缺少 instance_id: {path}")
    return out


def _synthetic_instances(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "instance_id": f"synth-{i:04d}-{uuid.uuid4().hex[:6]}",
            "repo": f"example/repo{i % 7}",
            "base_commit": "0" * 40,
            "problem_statement": f"synthetic task {i}",
            "patch": "",
            "test_patch": "",
            "seed": rng.randrange(1 << 30),
        }
        for i in range(n)
    ]


def _parse_markers(output: str) -> tuple[bool | None, int]:
    """解析 stdout 标记：返回 (resolved or None, gate_intercepts)。"""
    intercepts = 0
    resolved: bool | None = None
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith("PB_RESOLVED="):
            val = line.split("=", 1)[1].strip()
            if val in ("1", "true", "True"):
                resolved = True
            elif val in ("0", "false", "False"):
                resolved = False
        elif line.startswith("PB_GATE_INTERCEPTS="):
            try:
                intercepts = max(0, int(line.split("=", 1)[1].strip()))
            except ValueError:
                intercepts = 0
    return resolved, intercepts


def _run_agent(command: str, workdir: Path, timeout: float, env: dict) -> dict:
    import os

    start = time.monotonic()
    run_env = dict(os.environ)
    run_env.update(env)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(workdir),
            env=run_env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        resolved, intercepts = _parse_markers(output)
        if resolved is None:
            resolved = proc.returncode == 0
        return {
            "resolved": bool(resolved),
            "gate_intercepts": intercepts,
            "duration_s": round(time.monotonic() - start, 3),
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "resolved": False,
            "gate_intercepts": 0,
            "duration_s": round(time.monotonic() - start, 3),
            "error": f"timeout after {timeout}s",
        }


def run_benchmark(
    instances: list[dict],
    work_root: Path,
    cmd_baseline: str | None,
    cmd_gated: str | None,
    seed: int,
    timeout: float,
    synthetic: bool = False,
) -> dict:
    """编排评测：返回结构化报告（synthetic 模式用种子确定性模拟两组结果）。"""
    rows: list[dict] = []
    rng = random.Random(seed)
    for inst in instances:
        iid = str(inst["instance_id"])
        workdir = work_root / iid
        if not synthetic:
            workdir.mkdir(parents=True, exist_ok=True)
        row: dict = {
            "instance_id": iid,
            "repo": inst.get("repo", ""),
            "baseline": None,
            "gated": None,
        }
        if synthetic:
            base_resolved = rng.random() < 0.65
            gate_resolved = rng.random() < 0.85
            gate_intercepts = 1 if rng.random() < 0.9 else 0
            row["baseline"] = {
                "resolved": base_resolved,
                "gate_intercepts": 0,
                "duration_s": round(rng.uniform(20, 90), 3),
            }
            row["gated"] = {
                "resolved": gate_resolved,
                "gate_intercepts": gate_intercepts,
                "duration_s": round(rng.uniform(25, 110), 3),
            }
        else:
            env = {"PB_INSTANCE_ID": iid, "PB_WORKDIR": str(workdir)}
            if cmd_baseline:
                row["baseline"] = _run_agent(
                    cmd_baseline.replace("{id}", iid).replace("{workdir}", str(workdir)),
                    workdir,
                    timeout,
                    env,
                )
            if cmd_gated:
                row["gated"] = _run_agent(
                    cmd_gated.replace("{id}", iid).replace("{workdir}", str(workdir)),
                    workdir,
                    timeout,
                    env,
                )
        rows.append(row)

    def _aggregate(arm: str) -> dict:
        entries = [r[arm] for r in rows if r.get(arm)]
        if not entries:
            return {"runs": 0}
        resolved = sum(1 for e in entries if e["resolved"])
        intercepts = sum(int(e.get("gate_intercepts") or 0) for e in entries)
        durations = [float(e.get("duration_s") or 0.0) for e in entries]
        return {
            "runs": len(entries),
            "resolved": resolved,
            "resolve_rate": round(resolved / len(entries), 4),
            "gate_intercepts": intercepts,
            "gate_intercept_rate": round(intercepts / len(entries), 4),
            "avg_duration_s": round(sum(durations) / len(durations), 3),
        }

    baseline = _aggregate("baseline")
    gated = _aggregate("gated")
    return {
        "benchmark": "swebench-real-harness",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_instances": len(rows),
        "synthetic": synthetic,
        "baseline": baseline,
        "gated": gated,
        "summary": {
            "gated_resolve_above_baseline": (
                gated.get("resolve_rate", 0.0) >= baseline.get("resolve_rate", 0.0)
                if baseline.get("runs") and gated.get("runs")
                else None
            ),
            "gated_intercepted_any": bool(gated.get("gate_intercepts", 0) > 0),
        },
        "rows": rows,
    }


def check_thresholds(
    report: dict,
    require_gated_intercepts: bool = True,
    require_gated_ge_baseline: bool = True,
) -> list[str]:
    failures: list[str] = []
    base = report["baseline"]
    gate = report["gated"]
    if base.get("runs") and gate.get("runs"):
        if require_gated_ge_baseline and gate["resolve_rate"] < base["resolve_rate"]:
            failures.append(
                f"gated resolve 率 {gate['resolve_rate']:.1%} 低于 baseline "
                f"{base['resolve_rate']:.1%}"
            )
    if require_gated_intercepts and not gate.get("gate_intercepts", 0):
        failures.append("门禁组拦截数为 0（无法证明门禁生效）")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 SWE-bench 评测 harness（v0.41.0）")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--instances", type=Path, help="SWE-bench 风格实例清单 JSON")
    src.add_argument(
        "--synthetic", type=int, metavar="N", help="生成 N 个合成实例做冒烟/管线验证"
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子（确定性复现）")
    parser.add_argument("--cmd-baseline", default=None, help="基线 Agent 命令模板（含 {id}/{workdir}）")
    parser.add_argument("--cmd-gated", default=None, help="门禁 Agent 命令模板（含 {id}/{workdir}）")
    parser.add_argument("--work-root", type=Path, default=None, help="实例工作目录根（默认临时目录）")
    parser.add_argument("--timeout", type=float, default=1800.0, help="单个实例超时秒数")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", type=Path, default=None, help="写报告 JSON 到文件")
    parser.add_argument("--fail-fast", action="store_true", help="阈值不满足时退出码 1")
    parser.add_argument("--no-intercepts-check", action="store_true", help="关闭“门禁须有拦截”检查")
    args = parser.parse_args(argv)

    synthetic = args.synthetic is not None
    if not synthetic and (not args.cmd_baseline or not args.cmd_gated):
        parser.error("真实评测必须同时提供 --cmd-baseline 与 --cmd-gated")
    instances = _synthetic_instances(args.synthetic, args.seed) if synthetic else _load_instances(args.instances)
    import tempfile

    if args.work_root is not None:
        work_root = args.work_root
        work_root.mkdir(parents=True, exist_ok=True)
    else:
        work_root = Path(tempfile.mkdtemp(prefix="pb-swebench-"))
    report = run_benchmark(
        instances,
        work_root=work_root,
        cmd_baseline=args.cmd_baseline,
        cmd_gated=args.cmd_gated,
        seed=args.seed,
        timeout=args.timeout,
        synthetic=synthetic,
    )
    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {args.output}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        base, gate = report["baseline"], report["gated"]
        print(
            f"SWE-bench 评测（{'synthetic' if synthetic else 'real'}，"
            f"{report['n_instances']} 实例）"
        )
        print(f"  baseline: resolve {base.get('resolve_rate', float('nan')):.1%} "
              f"({base.get('resolved', 0)}/{base.get('runs', 0)})，"
              f"平均 {base.get('avg_duration_s', 0.0):.1f}s")
        print(f"  gated   : resolve {gate.get('resolve_rate', float('nan')):.1%} "
              f"({gate.get('resolved', 0)}/{gate.get('runs', 0)})，"
              f"拦截 {gate.get('gate_intercepts', 0)} 次，"
              f"平均 {gate.get('avg_duration_s', 0.0):.1f}s")
    if args.fail_fast:
        failures = check_thresholds(
            report,
            require_gated_intercepts=not args.no_intercepts_check,
        )
        if failures:
            print("评测未通过:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("评测通过：门禁组 resolve 不低于基线且存在拦截。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())