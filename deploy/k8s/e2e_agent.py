"""phase-barrier kind e2e：GateClient 经 sidecar 透明代理的完整门禁流程（v0.27.0）。

在 sidecar 容器内运行（``kubectl exec ... python - < e2e_agent.py``），
通过 ``http://localhost:8080`` 调用 ``/api/write`` / ``/api/exec`` / ``/api/advance``：

1. 未写 spec 直接写实现    -> GateDenied（跳步拦截）
2. 未写实现直接跑 pytest   -> GateDenied（跳步拦截）
3. 按 SOP：spec -> 测试 -> 实现 -> 测试 -> 交付，全部通过
"""
from __future__ import annotations

from anti_shortcut.proxy_client import GateClient, GateDenied

GATE_URL = "http://localhost:8080"

SPEC = """# 斐波那契函数 Spec

## 需求分析
需要一个函数 fib(n)，计算斐波那契数列第 n 项。F(0)=0, F(1)=1。

## 设计方案
采用迭代法，滚动维护前两项，时间复杂度 O(n)。

## 接口定义
def fib(n: int) -> int
"""

TESTS = '''"""测试用例"""
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55


def test_rejects_negative():
    import pytest

    with pytest.raises(ValueError):
        fib(-1)
'''

IMPL = '''def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''


def main() -> int:
    gate = GateClient(GATE_URL)
    print(f"[e2e] 连接 sidecar: {GATE_URL}")
    print(f"[e2e] 初始阶段: {gate.state()['stage_name']}")

    print("\n[e2e] 1) 未写 spec 直接写实现 -> 应被拦截")
    try:
        gate.write_file("fib.py", IMPL)
    except GateDenied as exc:
        print(f"       拦截成功: {exc}")
    else:
        print("[e2e] FAIL: 跳步未被拦截")
        return 1

    print("\n[e2e] 2) 未写实现直接跑 pytest -> 应被拦截")
    try:
        gate.execute_command("python -m pytest test_fib.py -q")
    except GateDenied as exc:
        print(f"       拦截成功: {exc}")
    else:
        print("[e2e] FAIL: 跳步未被拦截")
        return 1

    print("\n[e2e] 3) 按 SOP 推进：spec -> 测试 -> 实现")
    gate.write_file("spec.md", SPEC)
    gate.advance(2)
    print(f"       已进入阶段: {gate.state()['stage_name']}")
    gate.write_file("test_fib.py", TESTS)
    gate.advance(3)
    print(f"       已进入阶段: {gate.state()['stage_name']}")
    gate.write_file("fib.py", IMPL)
    gate.advance(4)
    print(f"       已进入阶段: {gate.state()['stage_name']}")

    print("\n[e2e] 4) 运行测试（经 /api/exec 代理，自动记录结果）")
    result = gate.execute_command("python -m pytest test_fib.py -q", timeout=60)
    print(f"       exit_code={result['exit_code']} recorded_test_run={result['recorded_test_run']}")

    print("\n[e2e] 5) 测试通过 -> 推进 -> 交付")
    gate.advance(5)
    state = gate.state()
    print(f"       当前阶段: {state['stage_name']} | 完成: {state['is_complete']}")
    if state["stage_name"] != "交付" or not state["is_complete"]:
        print("[e2e] FAIL: 未到达交付态")
        return 1

    print("\n[e2e] GateClient 全流程通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())