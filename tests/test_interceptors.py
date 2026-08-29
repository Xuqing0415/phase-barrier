"""工具拦截辅助模块测试。"""
from pathlib import Path

from anti_shortcut.config import GateConfig
from anti_shortcut.interceptors import (
    extract_written_paths,
    is_test_command,
    summarize_test_output,
    touches_gate_dir,
)


def test_is_test_command():
    cfg = GateConfig()
    assert is_test_command("pytest -q", cfg)
    assert is_test_command("python -m pytest tests/", cfg)
    assert is_test_command("python3 -m pytest", cfg)
    assert is_test_command("npm test", cfg)
    assert is_test_command("npx jest", cfg)
    assert is_test_command("cargo test", cfg)
    assert not is_test_command("ls -la", cfg)
    assert not is_test_command("cat spec.md", cfg)
    assert not is_test_command("", cfg)


def test_touches_gate_dir():
    gate = Path("ws/.agent_gate")
    assert touches_gate_dir("rm -rf .agent_gate", gate)
    assert touches_gate_dir("cat .agent_gate/state.json", gate)
    assert touches_gate_dir("cd .agent_gate && rm state.json", gate)
    assert touches_gate_dir("echo x > .agent_gate/state.json", gate)
    assert not touches_gate_dir("ls -la", gate)
    assert not touches_gate_dir("pytest", gate)


def test_extract_written_paths():
    assert extract_written_paths("echo hello > fib.py") == ["fib.py"]
    assert extract_written_paths("cmd >> test_fib.py") == ["test_fib.py"]
    assert extract_written_paths("sed -i s/a/b/ fib.py") == ["fib.py"]
    assert extract_written_paths("sed -i -e s/a/b/ fib.py") == ["fib.py"]
    assert extract_written_paths("mv a.py tests/test_b.py") == ["tests/test_b.py"]
    assert extract_written_paths("cp x.py y.py") == ["y.py"]
    assert extract_written_paths("rm -rf junk.py") == ["junk.py"]
    assert extract_written_paths("touch spec.md") == ["spec.md"]
    assert extract_written_paths("ls -la") == []
    assert extract_written_paths("cat spec.md") == []


def test_summarize_test_output():
    rec = summarize_test_output("..F\n1 failed, 2 passed in 0.1s", 1)
    assert rec["passed"] is False
    assert rec["exit_code"] == 1
    assert "1 failed" in rec["summary"]

    rec2 = summarize_test_output("3 passed in 0.1s", 0)
    assert rec2["passed"] is True
    assert "3 passed" in rec2["summary"]

    rec3 = summarize_test_output("2 failed", None)
    assert rec3["passed"] is False

    rec4 = summarize_test_output("5 passed", None)
    assert rec4["passed"] is True
