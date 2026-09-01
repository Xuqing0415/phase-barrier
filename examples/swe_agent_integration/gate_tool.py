"""SWE-agent 集成：经 phase-barrier 门禁的文件写 / 命令执行 CLI 包装（v0.27.0）。

SWE-agent 的工具可以“shell 到外部脚本”；把本脚本注册为工具后，SWE-agent
的写文件 / 执行命令都经过 sidecar 的 /api/write、/api/exec，跳步被拦截并返回
GATE_DENIED 消息。

用法（sidecar 地址用环境变量 PB_SIDECAR_URL 指定，默认 http://localhost:8080）：
  python gate_tool.py write <path> < content.txt
  python gate_tool.py exec <command>
  python gate_tool.py advance <stage>

运行：python examples/swe_agent_integration/gate_tool.py --self-test
"""
from __future__ import annotations

import os
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

def _gate_url() -> str:
    return os.environ.get("PB_SIDECAR_URL", "http://localhost:8080")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    gate = GateClient(_gate_url())
    cmd, *rest = argv
    try:
        if cmd == "write" and rest:
            path = rest[0]
            try:
                content = sys.stdin.read()
            except OSError:  # 测试/非交互环境下无 stdin
                content = ""
            gate.write_file(path, content)
            print(f"written: {path}")
            return 0
        if cmd == "exec" and rest:
            result = gate.execute_command(" ".join(rest))
            out = (result.get("output") or f"exit_code={result.get('exit_code')}")
            print(out.rstrip())
            return int(result.get("exit_code") or 0)
        if cmd == "advance" and rest:
            result = gate.advance(int(rest[0]))
            print(result.get("message") or result.get("error") or "")
            return 0 if result.get("success") else 1
        print(f"未知命令: {cmd}", file=sys.stderr)
        return 2
    except GateDenied as exc:
        print(f"GATE_DENIED: {exc}")
        return 3


def _self_test() -> int:
    """进程内 sidecar + CLI 子进程调用演示。"""
    ws = _make_workspace("pb-swe-demo-")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(ws, user_request="实现斐波那契函数")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    os.environ["PB_SIDECAR_URL"] = url
    spec = (
        "# 斐波那契函数 Spec\n\n"
        "## 需求分析\n"
        "实现一个函数 fib(n)，返回斐波那契数列第 n 项。约定 F(0)=0, F(1)=1，"
        "n 为自然数，负数输入应抛出 ValueError。\n\n"
        "## 设计方案\n"
        "采用迭代法，滚动维护前两项 a、b，时间复杂度 O(n)，空间复杂度 O(1)，"
        "避免递归导致的指数级开销和栈溢出风险。\n\n"
        "## 接口定义\n"
        "def fib(n: int) -> int — 返回第 n 项；n<0 时抛出 ValueError。\n"
    )
    tests = (
        '"""t"""\n'
        "from fib import fib\n\n"
        "def test_base_cases():\n"
        "    assert fib(0) == 0\n"
        "    assert fib(1) == 1\n\n"
        "def test_known_value():\n"
        "    assert fib(10) == 55\n"
    )
    impl = "def fib(n):\n    if n <= 1:\n        return n\n    a, b = 0, 1\n    for _ in range(n - 1):\n        a, b = b, a + b\n    return b\n"
    try:
        print("[self-test] 1) 未写 spec 直接写实现 -> GATE_DENIED")
        r = main(["write", "fib.py"])
        assert r == 3, r
        print("[self-test] 2) 按 SOP 推进")
        with open(ws / "spec.md", "w", encoding="utf-8") as fh:
            fh.write(spec)
        gate = GateClient(url)
        gate.write_file("spec.md", spec)
        gate.advance(2)
        gate.write_file("test_fib.py", tests)
        gate.advance(3)
        gate.write_file("fib.py", impl)
        gate.advance(4)
        print("[self-test] 3) 运行测试")
        r = main(["exec", "python", "-m", "pytest", "test_fib.py", "-q"])
        assert r == 0, r
        gate.advance(5)
        print("[self-test] 完成：", gate.state()["stage_name"])
        return 0
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(ws, ignore_errors=True)
        os.environ.pop("PB_SIDECAR_URL", None)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        raise SystemExit(_self_test())
    raise SystemExit(main(sys.argv[1:]))