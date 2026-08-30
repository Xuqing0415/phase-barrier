"""K8s sidecar 透明代理最小 demo（v0.17.0）。

自包含：在本进程内启动 sidecar HTTP 服务（ThreadingHTTPServer），
再用 ``GateClient`` 模拟一个“图快”的 Agent：

1. 未写 spec 就想写实现 fib.py      -> /api/write 拦截（403）
2. 未写实现就想跑 pytest            -> /api/exec 拦截（403）
3. 按 SOP：spec -> 测试 -> 实现 -> 测试 -> 交付，全部通过

真实 K8s 场景：sidecar 跑在独立容器，Agent 容器通过
http://localhost:8080 访问本客户端（见 deploy/k8s/gate-sidecar.yaml）。
"""
from __future__ import annotations

import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

from anti_shortcut.proxy_client import GateClient, GateDenied
from anti_shortcut.sidecar import GateSidecar, make_handler

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




def _make_workspace() -> Path:
    """创建可写工作区：优先系统临时目录，探测失败则回退到当前目录。"""
    try:
        ws = Path(mkdtemp(prefix="pb-proxy-demo-"))
        probe = ws / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return ws
    except OSError:
        ws = Path(".k8s_proxy_demo_ws")
        ws.mkdir(exist_ok=True)
        return ws


def main() -> int:
    workspace = _make_workspace()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(workspace, user_request="实现斐波那契函数")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
    try:
        print("[demo] 工作区:", workspace)
        print("[demo] 初始阶段:", gate.state()["stage_name"])

        print("\n[demo] 1) Agent 图快：未写 spec 直接写实现 -> 应被拦截")
        try:
            gate.write_file("fib.py", IMPL)
        except GateDenied as exc:
            print("       拦截成功:", exc)

        print("\n[demo] 2) Agent 图快：未写实现直接跑 pytest -> 应被拦截")
        try:
            gate.execute_command("python -m pytest test_fib.py -q")
        except GateDenied as exc:
            print("       拦截成功:", exc)

        print("\n[demo] 3) 按 SOP 推进：spec -> 测试 -> 实现")
        gate.write_file("spec.md", SPEC)
        gate.advance(2)
        print("       已进入阶段:", gate.state()["stage_name"])
        gate.write_file("test_fib.py", TESTS)
        gate.advance(3)
        print("       已进入阶段:", gate.state()["stage_name"])
        gate.write_file("fib.py", IMPL)
        gate.advance(4)
        print("       已进入阶段:", gate.state()["stage_name"])

        print("\n[demo] 4) 运行测试（经 /api/exec 代理，自动记录结果）")
        result = gate.execute_command("python -m pytest test_fib.py -q", timeout=60)
        print(
            "       exit_code =", result["exit_code"],
            "| recorded_test_run =", result["recorded_test_run"],
        )

        print("\n[demo] 5) 测试通过 -> 推进 -> 直接交付")
        gate.advance(5)
        state = gate.state()
        print("       当前阶段:", state["stage_name"], "| 完成:", state["is_complete"])
        return 0
    finally:
        server.shutdown()
        server.server_close()
        try:
            shutil.rmtree(workspace)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())