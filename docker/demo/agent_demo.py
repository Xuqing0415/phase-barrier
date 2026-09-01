"""phase-barrier 一键体验：模拟编码 Agent 的跳步与规范流程（Docker 演示用）。

展示两件事：
1. 跳步被拦截——在 spec / 测试用例未完成时写实现代码，write_file 抛出 PermissionError；
2. 规范流程全通——spec → 测试 → 实现 → 运行测试 → 推进到交付，每一步都由
   advance_stage 做证据校验。
"""
import os
import subprocess
from pathlib import Path

from anti_shortcut import AntiShortcutSkill

WS = Path(os.environ.get("PB_DEMO_WS", "/workspace"))

SPEC = """# 斐波那契数列

## 需求分析
实现函数 fib(n)，返回斐波那契数列第 n 项（n 从 0 开始）；n < 0 抛 ValueError。

## 设计方案
纯函数 + 递归实现；先校验参数，再计算。无副作用，便于单元测试。

## 接口定义
def fib(n: int) -> int
"""

TESTS = """import pytest
from fib import fib

def test_fib_base():
    assert fib(0) == 0
    assert fib(1) == 1

def test_fib_sequence():
    assert fib(10) == 55

def test_fib_negative():
    with pytest.raises(ValueError):
        fib(-1)
"""

IMPL = """def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    return n if n < 2 else fib(n - 1) + fib(n - 2)
"""


def real_write(path, content):
    target = Path(path)
    if not target.is_absolute():
        target = WS / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True}


def real_exec(command):
    proc = subprocess.run(command, shell=True, cwd=WS, capture_output=True, text=True)
    return {"exit_code": proc.returncode, "output": proc.stdout + proc.stderr}


def main() -> None:
    print("=" * 60)
    print(" phase-barrier 一键体验：模拟编码 Agent")
    print("=" * 60)
    skill = AntiShortcutSkill(WS, user_request="实现计算斐波那契数列的函数 fib(n)")
    tools = skill.install({"write_file": real_write, "execute_command": real_exec})
    print(f"\n[需求] 已记录用户需求，当前阶段：{skill.stage_name}（stage {skill.current_stage}）")

    print("\n[尝试跳步] Agent 试图跳过 spec / 测试，直接写实现 fib.py ...")
    try:
        tools["write_file"]("fib.py", IMPL)
        print("  !! 未被拦截（不应发生）")
    except PermissionError as exc:
        print(f"  [拦截] {exc}")

    print("\n[规范流程] 步骤 1：编写 spec.md 并通过校验 ...")
    tools["write_file"]("spec.md", SPEC)
    result = tools["advance_stage"](2)
    print(f"  advance_stage(2) -> {result['message']}")

    print("\n[规范流程] 步骤 2：编写测试 test_fib.py 并通过校验 ...")
    tools["write_file"]("test_fib.py", TESTS)
    result = tools["advance_stage"](3)
    print(f"  advance_stage(3) -> {result['message']}")

    print("\n[规范流程] 步骤 3：编写实现 fib.py 并通过语法检查 ...")
    tools["write_file"]("fib.py", IMPL)
    result = tools["advance_stage"](4)
    print(f"  advance_stage(4) -> {result['message']}")

    print("\n[规范流程] 步骤 4：运行测试 ...")
    run = tools["execute_command"]("python -m pytest test_fib.py -q")
    tail = (run.get("output") or "").strip().splitlines()
    print(f"  pytest 退出码 {run.get('exit_code')}，{tail[-1] if tail else ''}")

    result = tools["advance_stage"](5)
    print(f"  advance_stage(5) -> {result['message']}")

    print(f"\n[完成] 当前阶段：{skill.stage_name}（stage {skill.current_stage}）")
    print("跳步被拦截，规范流程全部通过 —— phase-barrier 门禁演示结束。")


if __name__ == "__main__":
    main()
