"""LangChain 集成：把 phase-barrier GateClient 包装成 Tool 可用的函数（v0.27.0）。

LangChain 的 ``Tool`` 可用 ``Tool.from_function(func=..., name=..., description=...)``
创建；本模块提供与 ``write_file`` / ``execute_command`` 一一对应的门禁函数，
返回 JSON 字符串（LangChain 工具约定）。被拦截时返回错误 JSON 而不是抛异常，
便于 LLM 读取拦截原因并修正流程。

核心逻辑仅依赖 ``anti_shortcut`` + 标准库，不强制安装 langchain。
接入 ``AgentExecutor`` 的完整步骤见 README.md。

运行：python examples/langchain_integration/gate_tools.py
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

from anti_shortcut.proxy_client import GateClient, GateDenied
from anti_shortcut.sidecar import GateSidecar, make_handler

def _make_workspace(prefix: str) -> Path:
    """创建可写工作区：优先系统临时目录，探测失败则回退到当前目录。"""
    try:
        ws = Path(mkdtemp(prefix=prefix))
        probe = ws / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return ws
    except OSError:
        ws = Path(prefix.rstrip("-"))
        ws.mkdir(exist_ok=True)
        return ws

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


def gate_write_file(gate: GateClient, path: str, content: str) -> str:
    """LangChain Tool 函数：经门禁写入文件，返回 JSON 结果或拦截信息。"""
    try:
        gate.write_file(path, content)
        return json.dumps({"ok": True, "path": path}, ensure_ascii=False)
    except GateDenied as exc:
        return json.dumps({"ok": False, "denied": str(exc)}, ensure_ascii=False)


def gate_execute_command(gate: GateClient, command: str) -> str:
    """LangChain Tool 函数：经门禁执行命令，返回 JSON 结果或拦截信息。"""
    try:
        result = gate.execute_command(command)
        return json.dumps({**result, "ok": True}, ensure_ascii=False)
    except GateDenied as exc:
        return json.dumps({"ok": False, "denied": str(exc)}, ensure_ascii=False)


def make_tools(gate: GateClient) -> dict:
    """返回 LangChain ``Tool.from_function`` 可直接消费的参数字典。"""
    return {
        "write_file": {
            "func": lambda path, content: gate_write_file(gate, path, content),
            "name": "gate_write_file",
            "description": (
                "经 phase-barrier 门禁写入工作区文件（参数：path 相对路径, content 内容）；"
                "跳步（如未完成 spec 就写实现）会被拒绝并说明原因"
            ),
        },
        "execute_command": {
            "func": lambda command: gate_execute_command(gate, command),
            "name": "gate_execute_command",
            "description": (
                "经 phase-barrier 门禁执行 shell 命令（测试命令自动记录结果）；"
                "跳步（如未完成实现就跑测试）会被拒绝并说明原因"
            ),
        },
    }


def demo() -> int:
    """进程内 sidecar + GateClient 演示：跳步拦截 + 规范流程全通。"""
    ws = _make_workspace("pb-langchain-demo-")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(ws, user_request="实现斐波那契函数")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
    try:
        tools = make_tools(gate)
        print("[demo] 1) 图快：未写 spec 直接写实现 -> 返回拦截信息")
        print("      ", tools["write_file"]["func"]("fib.py", IMPL))
        print("[demo] 2) 按 SOP：写 spec / 测试 / 实现")
        print("      ", tools["write_file"]["func"]("spec.md", SPEC))
        gate.advance(2)
        print("      ", tools["write_file"]["func"]("test_fib.py", TESTS))
        gate.advance(3)
        print("      ", tools["write_file"]["func"]("fib.py", IMPL))
        gate.advance(4)
        print("[demo] 3) 运行测试（经门禁执行）")
        print("      ", tools["execute_command"]["func"]("python -m pytest test_fib.py -q")[:200])
        gate.advance(5)
        print("[demo] 完成：", gate.state()["stage_name"])
        return 0
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(demo())