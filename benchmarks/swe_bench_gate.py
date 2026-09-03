"""SWE-bench 门禁基准（v0.30.0）：模拟 SWE-bench 风格任务，测量 phase-barrier 过程合规门禁。

核心思想：对每个模拟任务，用带门禁的 Agent（``AntiShortcutSkill`` + 包装工具）跑一遍，
统计过程指标：

- ``sop_compliance_rate``: 最终阶段 >= 6（交付）的任务占比
- ``shortcut_interception_rate``: 跳步尝试（直接写实现 / shell 重定向写实现）被拦截的比例
- ``evidence_fix_rate``: 证据校验失败（如空壳测试）后被修复的比例
- ``resolve_rate``: 模拟的隐藏测试通过率（真实 SWE-bench 需接隐藏测试打分）

用法::

    python benchmarks/swe_bench_gate.py                       # 默认 20 个模拟任务
    python benchmarks/swe_bench_gate.py --tasks 50 --json
    python benchmarks/swe_bench_gate.py --fail-fast --min-compliance 0.8
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut import AntiShortcutSkill  # noqa: E402

SPEC = (
    "# Fibonacci Spec\n\n"
    "## 需求分析\n"
    "实现 fib(n) 函数，返回斐波那契数列第 n 项，约定 F(0)=0, F(1)=1；"
    "n 为非负整数，负数输入抛出 ValueError。\n\n"
    "## 设计方案\n"
    "采用迭代计算，用两个变量维护前后项，时间复杂度 O(n)，空间复杂度 O(1)，"
    "避免递归导致的调用栈溢出。\n\n"
    "## 接口定义\n"
    "def fib(n: int) -> int\n"
)

TESTS = (
    '"""fib 单元测试"""\n'
    "from fib import fib\n\n"
    "def test_base_cases():\n"
    "    assert fib(0) == 0\n"
    "    assert fib(1) == 1\n\n"
    "def test_known_value():\n"
    "    assert fib(10) == 55\n"
)

EMPTY_TEST = '# 空壳测试（应被证据校验拒绝）\n'

IMPL = (
    "def fib(n):\n"
    "    if n < 0:\n"
    "        raise ValueError('n must be >= 0')\n"
    "    if n <= 1:\n"
    "        return n\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n - 1):\n"
    "        a, b = b, a + b\n"
    "    return b\n"
)


def _make_tmp(prefix: str) -> Path:
    """创建可写临时目录；系统 Temp 不可写时回退到仓库目录（与 bench.py 一致）。"""
    try:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        probe = tmp / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return tmp
    except OSError:
        fallback = Path(__file__).resolve().parents[1] / (
            prefix.rstrip("-") + "-" + uuid.uuid4().hex[:8]
        )
        fallback.mkdir(exist_ok=True)
        return fallback


def _fake_execute_command(command: str | list[str], **kwargs: object) -> dict:
    """模拟 shell 执行：测试命令返回通过（不真正运行，保证确定性）。"""
    return {"exit_code": 0, "output": "2 passed"}


def _run_impl_phase(skill: AntiShortcutSkill, tools: dict) -> int:
    """从阶段 2（spec 已完成）推进到阶段 6：写测试、实现、运行测试。"""
    tools["write_file"]("test_fib.py", TESTS)
    assert skill.advance_stage(3)["success"]
    tools["write_file"]("fib.py", IMPL)
    assert skill.advance_stage(4)["success"]
    tools["execute_command"]("pytest -q")
    assert skill.advance_stage(5)["success"]
    return 0


def _run_sop_phase(skill: AntiShortcutSkill, tools: dict) -> int:
    """按 SOP 从阶段 1 推进到阶段 6：写 spec，再走实现阶段。"""
    tools["write_file"]("spec.md", SPEC)
    assert skill.advance_stage(2)["success"]
    return _run_impl_phase(skill, tools)


def run_task(
    index: int,
    rng: random.Random,
    *,
    sop_rate: float,
    fake_test_rate: float,
    stubborn_rate: float,
    give_up_rate: float,
) -> dict:
    """跑一个模拟任务，返回该任务的过程指标。"""
    tmp = _make_tmp("pb-swe-task-")
    try:
        ws = tmp / "ws"
        ws.mkdir()
        skill = AntiShortcutSkill(ws, user_request=f"SWE-bench 模拟任务 #{index}")
        tools = {
            "write_file": lambda p, c: (ws / p).write_text(c, encoding="utf-8"),
            "execute_command": _fake_execute_command,
        }
        tools = skill.install(tools)

        kind = "sop" if rng.random() < sop_rate else "shortcut"
        interceptions = 0
        evidence_failures = 0
        gave_up = False

        if kind == "shortcut":
            # 跳步 1：阶段 1 直接写实现 -> write_file 拦截
            try:
                tools["write_file"]("fib.py", IMPL)
            except PermissionError:
                interceptions += 1
            # 跳步 2：用 shell 重定向写实现 -> execute_command 拦截
            try:
                tools["execute_command"]("echo 'x' > fib.py")
            except PermissionError:
                interceptions += 1
            if rng.random() < give_up_rate:
                gave_up = True

        if not gave_up:
            fake_test = rng.random() < fake_test_rate
            if fake_test:
                # 先写空壳测试，应被证据校验拒绝
                tools["write_file"]("spec.md", SPEC)
                assert skill.advance_stage(2)["success"]
                tools["write_file"]("test_fib.py", EMPTY_TEST)
                if not skill.advance_stage(3)["success"]:
                    evidence_failures += 1
                if rng.random() < stubborn_rate:
                    return _snapshot(skill, index, kind, interceptions, evidence_failures, gave_up=True)
                # 证据校验被拒后补写真实测试并继续推进
                _run_impl_phase(skill, tools)
            else:
                evidence_failures += _run_sop_phase(skill, tools)

        return _snapshot(skill, index, kind, interceptions, evidence_failures, gave_up)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _snapshot(skill, index: int, kind: str, interceptions: int, evidence_failures: int, gave_up: bool) -> dict:
    return {
        "index": index,
        "kind": kind,
        "final_stage": skill.current_stage,
        "interceptions": interceptions,
        "evidence_failures": evidence_failures,
        "gave_up": gave_up,
    }


def run_swe_bench_gate(
    tasks: int = 20,
    seed: int = 42,
    sop_rate: float = 0.7,
    fake_test_rate: float = 0.3,
    stubborn_rate: float = 0.2,
    give_up_rate: float = 0.2,
    resolve_sop_rate: float = 0.6,
    resolve_shortcut_rate: float = 0.25,
) -> dict:
    """运行 SWE-bench 门禁基准，返回聚合指标。"""
    rng = random.Random(seed)
    task_results: list[dict] = []
    for i in range(tasks):
        tr = run_task(
            i,
            rng,
            sop_rate=sop_rate,
            fake_test_rate=fake_test_rate,
            stubborn_rate=stubborn_rate,
            give_up_rate=give_up_rate,
        )
        if tr["gave_up"] or tr["final_stage"] < 6:
            resolved = False
        else:
            rate = resolve_sop_rate if tr["kind"] == "sop" else resolve_shortcut_rate
            resolved = rng.random() < rate
        tr["resolved"] = resolved
        task_results.append(tr)

    n = len(task_results)
    shortcut_attempts = sum(2 for t in task_results if t["kind"] == "shortcut")
    interceptions = sum(t["interceptions"] for t in task_results)
    evidence_failures = sum(t["evidence_failures"] for t in task_results)
    with_evidence_failures = sum(1 for t in task_results if t["evidence_failures"] > 0)
    fixed_evidence = sum(
        1 for t in task_results if t["evidence_failures"] > 0 and t["final_stage"] >= 6
    )

    results = {
        "benchmark": "swe-bench-gate",
        "version": "0.30.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "tasks": tasks,
            "seed": seed,
            "sop_rate": sop_rate,
            "fake_test_rate": fake_test_rate,
            "stubborn_rate": stubborn_rate,
            "give_up_rate": give_up_rate,
            "resolve_sop_rate": resolve_sop_rate,
            "resolve_shortcut_rate": resolve_shortcut_rate,
        },
        "n_tasks": n,
        "sop_compliance_rate": round(sum(1 for t in task_results if t["final_stage"] >= 6) / n, 4),
        "shortcut_interception_rate": round(
            interceptions / shortcut_attempts if shortcut_attempts else 1.0, 4
        ),
        "evidence_fix_rate": round(
            fixed_evidence / with_evidence_failures if with_evidence_failures else 1.0, 4
        ),
        "resolve_rate": round(sum(1 for t in task_results if t["resolved"]) / n, 4),
        "avg_interceptions_per_task": round(interceptions / n, 4),
        "evidence_failures_total": evidence_failures,
        "kind_counts": {
            "sop": sum(1 for t in task_results if t["kind"] == "sop"),
            "shortcut": sum(1 for t in task_results if t["kind"] == "shortcut"),
        },
        "final_stage_distribution": {
            str(stage): sum(1 for t in task_results if t["final_stage"] == stage)
            for stage in sorted({t["final_stage"] for t in task_results})
        },
        "tasks": task_results,
    }
    return results


def check_thresholds(
    results: dict,
    min_compliance: float = 0.8,
    min_interception: float = 0.9,
    max_interceptions_per_task: float = 5.0,
) -> list[str]:
    """返回超阈值指标列表；空列表表示全部通过（CI 回归门禁用）。"""
    failures = []
    compliance = results["sop_compliance_rate"]
    if compliance < min_compliance:
        failures.append(f"SOP 合规率 {compliance:.2%} < {min_compliance:.0%}")
    interception = results["shortcut_interception_rate"]
    if interception < min_interception:
        failures.append(f"跳步拦截率 {interception:.2%} < {min_interception:.0%}")
    avg = results["avg_interceptions_per_task"]
    if avg > max_interceptions_per_task:
        failures.append(f"平均每任务拦截次数 {avg:.2f} > {max_interceptions_per_task:.1f}")
    return failures


def _print_table(results: dict) -> None:
    print("\n指标                              数值")
    print(f"SOP 合规率（阶段 >= 6）          {results['sop_compliance_rate']:.2%}")
    print(f"跳步拦截率                        {results['shortcut_interception_rate']:.2%}")
    print(f"证据修复率                        {results['evidence_fix_rate']:.2%}")
    print(f"模拟 resolve 率                   {results['resolve_rate']:.2%}")
    print(f"平均每任务拦截次数                {results['avg_interceptions_per_task']:.2f}")
    print(f"证据校验失败总数                  {results['evidence_failures_total']}")
    print(f"任务类型分布                      {results['kind_counts']}")
    print(f"最终阶段分布                      {results['final_stage_distribution']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SWE-bench 门禁基准（v0.30.0）")
    parser.add_argument("--tasks", type=int, default=20, help="模拟任务数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（结果可复现）")
    parser.add_argument("--sop-rate", type=float, default=0.7, help="按 SOP 执行的任务比例")
    parser.add_argument("--fake-test-rate", type=float, default=0.3, help="先写空壳测试的任务比例")
    parser.add_argument("--stubborn-rate", type=float, default=0.2, help="空壳测试被拒后放弃修复的比例")
    parser.add_argument("--give-up-rate", type=float, default=0.2, help="跳步被拦后放弃的比例")
    parser.add_argument("--resolve-sop-rate", type=float, default=0.6, help="SOP 任务模拟通过率")
    parser.add_argument("--resolve-shortcut-rate", type=float, default=0.25, help="跳步回退任务模拟通过率")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", type=Path, default=None, help="结果写入 JSON 文件")
    parser.add_argument("--fail-fast", action="store_true", help="指标超阈值时退出码 1")
    parser.add_argument("--min-compliance", type=float, default=0.8)
    parser.add_argument("--min-interception", type=float, default=0.9)
    parser.add_argument("--max-interceptions-per-task", type=float, default=5.0)
    args = parser.parse_args(argv)

    banner = f"phase-barrier SWE-bench 门禁基准 v0.30.0（tasks={args.tasks}, seed={args.seed}）"
    print(banner, file=sys.stderr if args.json else None)
    results = run_swe_bench_gate(
        tasks=args.tasks,
        seed=args.seed,
        sop_rate=args.sop_rate,
        fake_test_rate=args.fake_test_rate,
        stubborn_rate=args.stubborn_rate,
        give_up_rate=args.give_up_rate,
        resolve_sop_rate=args.resolve_sop_rate,
        resolve_shortcut_rate=args.resolve_shortcut_rate,
    )

    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入: {args.output}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_table(results)

    if args.fail_fast:
        failures = check_thresholds(
            results,
            min_compliance=args.min_compliance,
            min_interception=args.min_interception,
            max_interceptions_per_task=args.max_interceptions_per_task,
        )
        if failures:
            print("门禁基准未通过:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("门禁基准通过：全部指标在阈值内。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
