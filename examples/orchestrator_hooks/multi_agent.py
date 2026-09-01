"""多 Agent 并发任务共享门禁状态示例（v0.26.3）。

场景：一个项目由多个 Agent 任务并发协作完成，共享同一个
``.agent_gate/state.json``：

- Agent A：编写 ``spec.md``（阶段 1 证据）并推进到阶段 2（测试用例编写）；
- Agent B：轮询等待阶段 2 放行后编写 ``test_fib.py`` 并推进到阶段 3（实现代码）；
- Agent C：轮询等待阶段 3 放行后编写 ``fib.py`` 并推进到阶段 4（运行测试）；
- 编排器主线程：运行测试、登记结果并推进到交付（阶段 6）。

并发安全由 ``StateManager``（v0.26.3）保证：

- 每个任务使用独立的 ``PhaseBarrier`` 实例（模拟独立进程 / Agent）；
- 状态“读-改-写”经文件锁（POSIX flock / Windows msvcrt）+ 写前重载：
  阶段推进串行化、不丢更新、状态文件不损坏；
- 锁文件 ``state.json.lock`` 位于门禁目录，Agent 无法写入；
- 轮询读取方通过 ``PhaseBarrier.refresh()`` 读到他人推进结果。

运行：python examples/orchestrator_hooks/multi_agent.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from anti_shortcut import PhaseBarrier  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent / "multi_agent_workspace"

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


def wait_stage(barrier: PhaseBarrier, stage: int, timeout: float = 30.0) -> bool:
    """编排器钩子：轮询等待状态机推进到 ``stage`` 且该阶段门禁放行。

    每次轮询先 ``refresh()`` 读取其他 Agent 的推进结果，再结合
    ``check(stage)`` 校验前置证据。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        barrier.refresh()
        if barrier.inspect()["current_stage"] >= stage and barrier.check(stage)["allowed"]:
            return True
        time.sleep(0.05)
    return False


def agent_spec(ws: Path) -> str:
    """Agent A：阶段 1（spec 设计）证据 -> 推进到阶段 2。"""
    b = PhaseBarrier(workspace=ws)
    try:
        if not wait_stage(b, 1):
            return "spec: 等待阶段 1 放行超时"
        write(ws, "spec.md", SPEC)
        r = b.advance(2)
        return f"spec: {'推进成功 -> 阶段 2' if r['success'] else r['error']}"
    finally:
        b.close()


def agent_tests(ws: Path) -> str:
    """Agent B：阶段 2（测试用例）证据 -> 推进到阶段 3。"""
    b = PhaseBarrier(workspace=ws)
    try:
        if not wait_stage(b, 2):
            return "tests: 等待阶段 2 放行超时"
        write(ws, "test_fib.py", TESTS)
        r = b.advance(3)
        return f"tests: {'推进成功 -> 阶段 3' if r['success'] else r['error']}"
    finally:
        b.close()


def agent_impl(ws: Path) -> str:
    """Agent C：阶段 3（实现代码）证据 -> 推进到阶段 4。"""
    b = PhaseBarrier(workspace=ws)
    try:
        if not wait_stage(b, 3):
            return "impl: 等待阶段 3 放行超时"
        write(ws, "fib.py", IMPL)
        r = b.advance(4)
        return f"impl: {'推进成功 -> 阶段 4' if r['success'] else r['error']}"
    finally:
        b.close()


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

    print("=" * 72)
    print("多 Agent 并发任务共享门禁状态示例（v0.26.3）")
    print("=" * 72)

    # ---- 0：跳步拦截演示 ----
    print("\n[编排器] 跳步演示：任务刚启动，Agent 声称直接进入阶段 6（交付）")
    gate = barrier.check(6)
    print(f"        -> check(6) allowed={gate['allowed']}  {gate['message']}")
    denied = barrier.advance(6)
    print(f"        -> advance(6) success={denied['success']}  {denied['error']}")

    # ---- 1：三个 Agent 并发协作（各自独立 PhaseBarrier 实例） ----
    print("\n[编排器] 派发 3 个并发 Agent 任务（spec / tests / impl），共享同一状态文件")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(agent_spec, WORKSPACE): "Agent A (spec)",
            pool.submit(agent_tests, WORKSPACE): "Agent B (tests)",
            pool.submit(agent_impl, WORKSPACE): "Agent C (impl)",
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                print(f"        [{name}] {fut.result()}")
            except Exception as exc:  # noqa: BLE001
                print(f"        [{name}] 异常: {exc}")

    # ---- 2：编排器运行测试并推进 ----
    print("\n[编排器] 运行测试并登记结果")
    run_result = run_pytest(WORKSPACE)
    barrier.record_test_run(run_result)
    r = barrier.advance(5)
    print(f"        -> {'推进成功 -> 阶段 6（交付）' if r['success'] else r['error']}")

    # ---- 3：并发写入压力演示（不损坏、不丢更新） ----
    print("\n[编排器] 6 个并发 record_test_run 写入压力演示")
    runs = [{"exit_code": i % 2, "output": f"stress-{i}"} for i in range(6)]

    def stress(i: int) -> None:
        local = PhaseBarrier(workspace=WORKSPACE)
        try:
            local.record_test_run(runs[i])
        finally:
            local.close()

    threads = [threading.Thread(target=stress, args=(i,)) for i in range(len(runs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = barrier.refresh()  # 读取其他实例的最新写入
    last = snap["last_test_run"]
    matched = last is not None and any(
        last.get("exit_code") == r2["exit_code"] and last.get("summary") == f"stress-{i}"
        for i, r2 in enumerate(runs)
    )
    print(f"        -> last_test_run 完整落在某一次写入: {matched}")

    # ---- 收尾 ----
    print("\n" + "=" * 72)
    print(f"最终状态：阶段 {snap['current_stage']}（{snap['stage_name']}）"
          f" completed={snap['completed_stages']} complete={snap['complete']}")
    ev = barrier.verify_evidence()
    print(f"证据清单校验：ok={ev['ok']} signed={ev['signed']} violations={ev['violations']}")
    print("=" * 72)
    barrier.close()
    ok = (snap["current_stage"] == 6 and snap["complete"] and ev["ok"] and matched)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())