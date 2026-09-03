"""LangChain 集成最小演示（v0.32.0）。

展示两种场景（进程内 sidecar + GateClient，无需真实模型 / 网络）：

  1. 图快：跳过 spec / 测试直接写实现代码 -> 被 phase-barrier 拦截（denied JSON）
  2. 按 SOP：spec -> advance -> 测试 -> advance -> 实现 -> 测试运行 -> advance -> 交付

langchain-core 已安装时走真实 ``BaseTool`` 路径（``PhaseBarrierWriteTool.invoke``）；
未安装时回退到 ``gate_tools`` 的工具函数路径，脚本仍可完整运行。

运行：python examples/langchain_integration/demo.py
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anti_shortcut.proxy_client import GateClient  # noqa: E402
from anti_shortcut.sidecar import GateSidecar, make_handler  # noqa: E402

import phase_barrier_tool as pbt  # noqa: E402
from gate_tools import IMPL, SPEC, TESTS, _make_workspace, make_tools  # noqa: E402


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _loads(value: object) -> dict:
    """工具返回 JSON 字符串（LangChain 约定），统一解析为 dict。"""
    return json.loads(value) if isinstance(value, str) else dict(value)


def _run_flow(gate: GateClient, use_basetool: bool) -> int:
    """跳步拦截 + 按 SOP 全通到交付。"""
    if use_basetool:
        write_tool = pbt.PhaseBarrierWriteTool(gate=gate)
        exec_tool = pbt.PhaseBarrierExecTool(gate=gate)
        _check(
            isinstance(write_tool, pbt.PhaseBarrierTool),
            "PhaseBarrierWriteTool 应为 PhaseBarrierTool（BaseTool）子类",
        )

        def do_write(path: str, content: str) -> dict:
            return _loads(write_tool.invoke({"path": path, "content": content}))

        def do_exec(command: str) -> dict:
            return _loads(exec_tool.invoke({"command": command}))

    else:
        tools = make_tools(gate)

        def do_write(path: str, content: str) -> dict:
            return _loads(tools["write_file"]["func"](path, content))

        def do_exec(command: str) -> dict:
            return _loads(tools["execute_command"]["func"](command))

    print("[demo 1/3] 图快：未写 spec 直接写实现 fib.py")
    denied = do_write("fib.py", IMPL)
    _check(denied.get("ok") is False and "denied" in denied, f"跳步应被拦截: {denied}")

    print("[demo 2/3] 按 SOP：spec.md -> advance -> test_fib.py -> advance -> fib.py -> advance")
    spec = do_write("spec.md", SPEC)
    _check(spec.get("ok") is True, f"spec 写入失败: {spec}")
    gate.advance(2)
    tests = do_write("test_fib.py", TESTS)
    _check(tests.get("ok") is True, f"测试写入失败: {tests}")
    gate.advance(3)
    impl = do_write("fib.py", IMPL)
    _check(impl.get("ok") is True, f"实现写入失败: {impl}")
    gate.advance(4)

    print("[demo 3/3] 运行测试（经门禁执行）")
    out = do_exec("python -m pytest test_fib.py -q")
    _check(out.get("ok") is True and out.get("exit_code") == 0, f"测试运行失败: {out}")
    gate.advance(5)
    stage = gate.state()["stage_name"]
    _check(stage == "交付", f"最终阶段应为交付，实际: {stage}")
    path_label = "BaseTool（langchain-core）路径" if use_basetool else "gate_tools 工具函数路径"
    print(f"[demo] 完成：{stage}（{path_label}）")
    return 0


def main() -> int:
    ws = _make_workspace("pb-langchain-demo-")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(ws, user_request="实现斐波那契函数")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
    try:
        if pbt.HAS_LANGCHAIN:
            print("[langchain-demo] langchain-core 已安装 -> 走真实 BaseTool 路径")
            return _run_flow(gate, use_basetool=True)
        print("[langchain-demo] langchain-core 未安装 -> 回退 gate_tools 函数路径")
        print("（pip install 'langchain-core>=0.3' 后即可验证 BaseTool 子类路径）")
        return _run_flow(gate, use_basetool=False)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
