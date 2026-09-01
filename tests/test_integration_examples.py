"""Agent 框架集成示例测试（v0.27.0）。

运行三个自包含示例的进程内 sidecar demo：
- LangChain：gate_write_file / gate_execute_command 包装（跳步返回拦截 JSON）
- AutoGPT：write_file / execute_shell 命令包装（跳步返回 GATE_DENIED）
- SWE-agent：gate_tool CLI（--self-test）

不依赖 langchain / autogpt / swe-agent 第三方包。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _load(module_name: str, rel: str):
    path = EXAMPLES / rel
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_langchain_gate_tools_demo():
    mod = _load("pb_lc_tools", "langchain_integration/gate_tools.py")
    assert mod.demo() == 0


def test_autogpt_command_wrapper_demo():
    mod = _load("pb_ag_wrapper", "autogpt_integration/gate_command_wrapper.py")
    assert mod.demo() == 0


def test_swe_agent_gate_tool_self_test():
    mod = _load("pb_swe_tool", "swe_agent_integration/gate_tool.py")
    assert mod._self_test() == 0


def test_langchain_wrapper_blocks_jump_and_passes_sop(tmp_path):
    """直接断言 make_tools 包装语义：跳步返回 denied JSON，规范流程 ok=True。"""
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from anti_shortcut.proxy_client import GateClient
    from anti_shortcut.sidecar import GateSidecar, make_handler

    mod = _load("pb_lc_tools2", "langchain_integration/gate_tools.py")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(GateSidecar(tmp_path, user_request="实现斐波那契函数")),
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
    try:
        tools = mod.make_tools(gate)
        denied = json.loads(tools["write_file"]["func"]("fib.py", mod.IMPL))
        assert denied["ok"] is False and "denied" in denied
        assert json.loads(tools["write_file"]["func"]("spec.md", mod.SPEC))["ok"] is True
        gate.advance(2)
        assert json.loads(tools["write_file"]["func"]("test_fib.py", mod.TESTS))["ok"] is True
        gate.advance(3)
        assert json.loads(tools["write_file"]["func"]("fib.py", mod.IMPL))["ok"] is True
        gate.advance(4)
        out = json.loads(tools["execute_command"]["func"]("python -m pytest test_fib.py -q"))
        assert out["ok"] is True and out["exit_code"] == 0
        gate.advance(5)
        assert gate.state()["stage_name"] == "交付"
    finally:
        server.shutdown()
        server.server_close()
