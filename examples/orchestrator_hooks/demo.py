"""编排器钩子集成示例（v0.22.0）：Alpha-SWE 等平台以“轻量 SDK”接入 phase-barrier。

演示在任务启动 / 阶段切换两个钩子调用 ``PhaseBarrier``：
- 任务启动钩子：Agent 声称从阶段 1（spec 设计）开始 -> ``barrier.check(1)``；
- 阶段切换钩子：Agent 声称完成某阶段并申请进入下一阶段 -> ``barrier.advance(N)``。

Agent 跳步（未写 spec 直接声称进入实现阶段）会在钩子处被拦截，
约束消息回传给 Agent 强制补全前置证据。文件写入由编排器放行
（本示例直接写文件系统），门禁只负责阶段校验 —— 与工具级拦截
（``AntiShortcutSkill.install``）互补，可叠加使用。

运行：python examples/orchestrator_hooks/demo.py

v0.26.2 起附带辅助查询演示：``list_stages()``（阶段清单）与
``stage_of(path)``（文件 -> 阶段证据归属，与 ``verify-evidence --git-base``
的 ``git_impact`` 分类一致）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from anti_shortcut import PhaseBarrier  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent / "demo_workspace"

USER_REQUEST = "实现一个计算斐波那契数列第 n 项的函数 fib(n)，F(0)=0, F(1)=1，n<0 抛 ValueError。"

SPEC = """# 斐波那契数列函数 Spec

## 需求分析
用户需要一个函数 fib(n)，返回斐波那契数列第 n 项。
- F(0) = 0, F(1) = 1, F(n) = F(n-1) + F(n-2)
- n 为非负整数；n < 0 或非整数输入时抛出 ValueError。
- 时间复杂度要求 O(n)，避免指数级递归。

## 设计方案
使用迭代法维护前两项，避免递归栈与重复计算。
函数签名为 def fib(n: int) -> int。

## 接口定义
- 输入：n: int，非负整数。
- 输出：int，第 n 项斐波那契数。
- 异常：n < 0 或非法类型时抛出 ValueError。
- 边界：fib(0) == 0, fib(1) == 1, fib(10) == 55。
"""

TESTS = '''"""test_fib.py - 斐波那契函数单元测试"""
import pytest
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55


def test_rejects_negative():
    with pytest.raises(ValueError):
        fib(-1)
'''

IMPL = '''def fib(n: int) -> int:
    """返回斐波那契数列第 n 项。"""
    if not isinstance(n, int) or n < 0:
        raise ValueError("n must be a non-negative integer")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''


def write(ws: Path, rel: str, content: str) -> None:
    """编排器放行的文件写入（真实文件系统）。"""
    target = ws / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def run_pytest(ws: Path) -> dict:
    """编排器执行的测试命令（真实 shell）。"""
    proc = subprocess.run(
        "python -m pytest test_fib.py -q -p no:cacheprovider",
        shell=True,
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}


def main() -> int:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True)
    (WORKSPACE / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")

    barrier = PhaseBarrier(workspace=WORKSPACE, user_request=USER_REQUEST)

    def hook(name: str, result: dict) -> None:
        if result.get("success") is not None:
            status = "OK" if result["success"] else "REJECTED"
            detail = result.get("message") or result.get("error") or ""
        elif result.get("allowed") is not None:
            status = "OK" if result["allowed"] else "BLOCKED"
            detail = result.get("message") or ""
        else:
            status, detail = "OK", str(result)
        print(f"[{status}] {name}")
        if detail:
            print(f"        -> {detail}")
        for v in result.get("violations", []):
            print(f"        - violation: {v}")

    print("=" * 72)
    print("编排器钩子集成示例：任务启动 / 阶段切换 两个钩子调用 PhaseBarrier")
    print("=" * 72)

    # ---- 任务启动钩子 ----
    print("\n[编排器] 任务启动：Agent 声称从阶段 1（spec 设计）开始")
    hook("check(1) 任务启动钩子", barrier.check(1))

    # ---- Agent 尝试跳步 ----
    print("\n[编排器] Agent 声称直接进入阶段 3（实现代码），但尚未编写 spec")
    hook("check(3) 应被拦截", barrier.check(3))
    print("[编排器] 把约束消息回传给 Agent ->", barrier.check(3)["message"])

    # ---- 按 SOP 推进 ----
    print("\n[编排器] Agent 编写 spec.md")
    write(WORKSPACE, "spec.md", SPEC)
    hook("advance(2) 阶段切换：spec -> 测试", barrier.advance(2))

    print("\n[编排器] Agent 编写 test_fib.py")
    write(WORKSPACE, "test_fib.py", TESTS)
    hook("advance(3) 阶段切换：测试 -> 实现", barrier.advance(3))

    print("\n[编排器] Agent 编写 fib.py")
    write(WORKSPACE, "fib.py", IMPL)
    hook("advance(4) 阶段切换：实现 -> 运行测试", barrier.advance(4))

    print("\n[编排器] Agent 运行测试")
    run_result = run_pytest(WORKSPACE)
    hook("record_test_run(exit_code, output) 登记测试结果", barrier.record_test_run(run_result))
    hook("advance(5) 阶段切换：测试通过 -> 交付", barrier.advance(5))

    # ---- 辅助查询（v0.26.2）：阶段清单 + 文件归属 ----
    print("\n[编排器] 辅助查询 list_stages() / stage_of(path)")
    stages = barrier.list_stages()
    print(f"        -> 阶段清单共 {len(stages)} 个："
          + ", ".join(f"{s['stage']} {s['name']}" for s in stages))
    for rel in ("spec.md", "test_fib.py", "fib.py", "README.md"):
        info = barrier.stage_of(rel)
        print(f"        -> stage_of({rel}) = 阶段 {info['stage']}（{info['kind']}）")

    # ---- 收尾：状态快照 + 证据校验 ----
    print("\n" + "=" * 72)
    snapshot = barrier.inspect()
    print(
        f"最终状态：阶段 {snapshot['current_stage']}（{snapshot['stage_name']}）"
        f" completed={snapshot['completed_stages']} complete={snapshot['complete']}"
    )
    ev = barrier.verify_evidence()
    print(f"证据清单校验：ok={ev['ok']} signed={ev['signed']} violations={ev['violations']}")
    print("=" * 72)
    barrier.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
