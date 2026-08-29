"""Gate-keeper：在 /workspace 中完成一次完整的门禁流程，生成可审计的交付态。

演示目的：先由该服务（对 .agent_gate 可写）推进状态到阶段 6（交付），
随后 agent 服务以只读方式挂载 .agent_gate，验证其无法篡改状态。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from anti_shortcut import AntiShortcutSkill

WORKSPACE = Path("/workspace")

SPEC = """# \u6590\u6ce2\u90a3\u5951\u51fd\u6570 Spec

## \u9700\u6c42\u5206\u6790
\u9700\u8981\u4e00\u4e2a\u51fd\u6570 fib(n)\uff0c\u8ba1\u7b97\u6590\u6ce2\u90a3\u5951\u6570\u5217\u7b2c n \u9879\u3002

## \u8bbe\u8ba1\u65b9\u6848
\u91c7\u7528\u8fed\u4ee3\u6cd5\uff0c\u6eda\u52a8\u7ef4\u62a4\u524d\u4e24\u9879\uff0c\u65f6\u95f4\u590d\u6742\u5ea6 O(n)\u3002

## \u63a5\u53e3\u5b9a\u4e49
def fib(n: int) -> int
"""

TESTS = '''import pytest
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


def write_file(path, content):
    p = WORKSPACE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True}


def execute_command(command):
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=WORKSPACE)
    return {"exit_code": proc.returncode, "output": proc.stdout + proc.stderr}


def main() -> int:
    (WORKSPACE / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    tools = {"write_file": write_file, "execute_command": execute_command}
    skill = AntiShortcutSkill(WORKSPACE, user_request="Docker \u6f14\u793a\uff1a\u5b9e\u73b0\u6590\u6ce2\u90a3\u5951\u51fd\u6570")
    skill.install(tools)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"], "spec \u9636\u6bb5\u63a8\u8fdb\u5931\u8d25"
    tools["write_file"]("test_fib.py", TESTS)
    assert tools["advance_stage"](3)["success"], "\u6d4b\u8bd5\u9636\u6bb5\u63a8\u8fdb\u5931\u8d25"
    tools["write_file"]("fib.py", IMPL)
    assert tools["advance_stage"](4)["success"], "\u5b9e\u73b0\u9636\u6bb5\u63a8\u8fdb\u5931\u8d25"
    r = tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert r["exit_code"] == 0, r["output"]
    assert tools["advance_stage"](5)["success"], "\u6d4b\u8bd5\u9636\u6bb5\u63a8\u8fdb\u5931\u8d25"

    print(f"[gate-keeper] \u5b8c\u6210\uff1a\u5f53\u524d\u9636\u6bb5 {skill.current_stage}\uff08{skill.stage_name}\uff09")
    print(f"[gate-keeper] \u72b6\u6001\u6587\u4ef6\uff1a{WORKSPACE / '.agent_gate' / 'state.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
