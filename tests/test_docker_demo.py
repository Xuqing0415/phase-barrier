"""docker/demo/agent_demo.py 端到端测试（回归：一键体验主路径此前无覆盖）。

`docker/demo/agent_demo.py` 是「零安装体验」的核心脚本（README 与
`docker/demo/README.md` 首推的 `docker run ... phase-barrier-demo` 实际执行它），
但它此前没有任何测试覆盖，于是下面这个崩溃一直没被发现：

    proc = subprocess.run(..., text=True)          # 未指定 encoding
    return {"output": proc.stdout + proc.stderr}   # 解码失败时 stdout 为 None -> TypeError

在非 UTF-8 locale（如 Windows 中文环境的 GBK）下，子进程 pytest 输出里只要有
UTF-8 字节无法按 GBK 解码，`subprocess` 就会把 `stdout` / `stderr` 置为 `None`，
演示脚本直接中断在最后一步（实测复现）。修复后既显式指定 `encoding="utf-8"`，
也对 `None` 做兜底。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docker" / "demo" / "agent_demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("pb_docker_agent_demo", DEMO)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_docker_agent_demo"] = module
    spec.loader.exec_module(module)
    return module


def _demo_env(tmp_path: Path) -> dict:
    """演示脚本用 shell 调 `python -m pytest`，需保证 `python` 指向当前解释器。"""
    env = dict(os.environ)
    env["PB_DEMO_WS"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


@pytest.mark.skipif(not DEMO.exists(), reason="demo script missing")
def test_agent_demo_runs_end_to_end(tmp_path):
    """一键体验脚本能跑完，且既有拦截又有全流程通过。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proc = subprocess.run(
        [sys.executable, str(DEMO)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_demo_env(ws),
        timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out

    assert "[拦截]" in out, out                      # 跳步被门禁拦住
    assert "阶段 6" in out, out                      # 规范流程走到交付
    assert (ws / "spec.md").exists()
    assert (ws / "test_fib.py").exists()
    assert (ws / "fib.py").exists()


def test_real_exec_tolerates_undecodable_child_output(tmp_path):
    """回归：子进程输出无法解码时，real_exec 必须返回字符串而不是抛 TypeError。"""
    module = _load_demo()
    module.WS = tmp_path

    helper = tmp_path / "emit_bad_bytes.py"
    helper.write_bytes(
        b"import sys\n"
        b"sys.stdout.buffer.write(b'\\xff\\xfe\\x80bad-bytes')\n"
        b"sys.stderr.buffer.write(b'\\x80also-bad')\n"
    )
    result = module.real_exec(f'"{sys.executable}" "{helper}"')

    assert result["exit_code"] == 0
    assert isinstance(result["output"], str)
    assert "bad-bytes" in result["output"]
