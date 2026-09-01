"""AutoGPT 集成：把命令注册表中的文件写 / 命令执行替换为经 phase-barrier 门禁的版本。

AutoGPT 通过命令注册表（CommandRegistry）注册 ``execute_shell`` / ``write_file``
等命令。本模块提供 ``install(gate, commands)``：对已知命令名做包装——先经
GateClient 校验，被拦截时返回明确错误消息而不是执行，跳步的 Agent 行为被阻断。

核心逻辑仅依赖 ``anti_shortcut`` + 标准库；命令签名与 AutoGPT 社区版
``execute_command(name, args)`` 风格对齐，接入点见 README.md。

运行：python examples/autogpt_integration/gate_command_wrapper.py
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

# 需要被门禁包装的命令名 -> 参数中表示“路径”的键（用于展示，可扩展）
WRAPPED = {
    "execute_shell": "command",
    "write_file": "filename",
    "read_file": "filename",
    "file_operations": "path",
}

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


def _command_name(name: str, args: dict) -> str:
    """把 AutoGPT 风格命令名映射到 GateClient 动作（write/exec/read）。"""
    lower = (name or "").lower()
    if "write" in lower or "file_operations" == lower and args.get("action") in ("write", "append"):
        return "write"
    if "shell" in lower or "exec" in lower or "command" in lower:
        return "exec"
    return "read"


def install(gate: GateClient, commands: dict) -> dict:
    """包装 AutoGPT 命令表：返回与原表同构的新命令表，被拦截命令返回错误消息。

    :param commands: 命令名 -> 可调用对象（``fn(args) -> str``）的映射。
    :return: 新命令表（原表不被修改）。
    """
    wrapped: dict = {}

    def make(name: str, original) -> callable:
        def guarded(args: dict) -> str:
            action = _command_name(name, args or {})
            try:
                if action == "write":
                    a = args or {}
                    path = str(
                        a.get("filename") or a.get("path") or a.get("file_path") or ""
                    )
                    content = str(a.get("content", "") or "")
                    gate.write_file(path, content)
                    return f"文件已经门禁写入: {path}"
                if action == "exec":
                    command = str((args or {}).get(WRAPPED.get(name, "command") or "command", ""))
                    result = gate.execute_command(command)
                    return (result.get("output") or f"exit_code={result.get('exit_code')}")
                return original(args) if original else f"[{name}] 只读命令未包装"
            except GateDenied as exc:
                return f"GATE_DENIED: {exc}"

        return guarded

    for name, original in commands.items():
        if name in WRAPPED:
            wrapped[name] = make(name, original)
        else:
            wrapped[name] = original
    return wrapped


def demo() -> int:
    """进程内 sidecar + 命令包装演示：跳步拦截 + 规范流程全通。"""
    ws = _make_workspace("pb-autogpt-demo-")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(ws, user_request="实现斐波那契函数")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])

    # 模拟 AutoGPT 命令表：write_file / execute_shell 的真实实现（此处直写直跑）
    def real_write(args):
        target = ws / str(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        return f"wrote {args['path']}"

    def real_exec(args):
        import subprocess
        proc = subprocess.run(str(args["command"]), shell=True, cwd=ws, capture_output=True, text=True)
        return proc.stdout + proc.stderr

    commands = {"write_file": real_write, "execute_shell": real_exec}
    wrapped = install(gate, commands)
    try:
        print("[demo] 1) 图快：未写 spec 直接写实现 -> 命令返回 GATE_DENIED")
        print("      ", wrapped["write_file"]({"path": "fib.py", "content": IMPL}))
        print("[demo] 2) 按 SOP：spec -> 测试 -> 实现")
        print("      ", wrapped["write_file"]({"path": "spec.md", "content": SPEC}))
        gate.advance(2)
        print("      ", wrapped["write_file"]({"path": "test_fib.py", "content": TESTS}))
        gate.advance(3)
        print("      ", wrapped["write_file"]({"path": "fib.py", "content": IMPL}))
        gate.advance(4)
        print("[demo] 3) 运行测试（经门禁执行）")
        out = wrapped["execute_shell"]({"command": "python -m pytest test_fib.py -q"})
        print("      ", out.strip().splitlines()[-1] if out.strip() else out)
        gate.advance(5)
        print("[demo] 完成：", gate.state()["stage_name"])
        return 0
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(demo())