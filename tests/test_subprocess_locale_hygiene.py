"""静态守护：`subprocess` 文本模式必须显式指定 `encoding`。

`subprocess.run(..., text=True)` 不带 `encoding=` 会按**当前 locale** 解码子进程
输出。在 Windows（GBK）等非 UTF-8 环境下，只要子进程输出含无法解码的字节，
`subprocess` 就抛 `UnicodeDecodeError` 并把 `stdout` / `stderr` 留成 `None`，
调用方常见的 `proc.stdout + proc.stderr` 随即 `TypeError`。

v0.56.0 曾因此让官方一键体验 `docker run ... phase-barrier-demo` 在最后一步中断
（`docker/demo/agent_demo.py`），同类写法还散落在 `deploy/seed_gate.py`、
`examples/autogpt_integration/gate_command_wrapper.py`、`scripts/run_swebench_batch.py`。
本测试把「必须写 encoding」固化成守护，避免回归。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"subprocess\.(run|Popen|check_output|check_call|call)\s*\(")

SKIP_PREFIXES = ("tests/", "tests\\")


def _tracked_python_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:  # pragma: no cover - 无 git 的极端环境
        out = None
    if out is not None and out.returncode == 0 and out.stdout.strip():
        names = out.stdout.split()
    else:  # pragma: no cover - 回退路径
        names = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")]
    files = []
    for name in names:
        name = name.replace("\\", "/")
        if name.startswith(SKIP_PREFIXES) or "/tests/" in name:
            continue
        p = ROOT / name
        if p.is_file():
            files.append(p)
    return files


def _call_args(text: str, start: int) -> str:
    """取出从 start 处左括号起、括号配平的一段调用参数字面量。"""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def test_subprocess_text_mode_specifies_encoding():
    offenders = []
    for path in _tracked_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in CALL.finditer(text):
            args = _call_args(text, match.end() - 1)
            if "text=True" not in args and "universal_newlines=True" not in args:
                continue
            if "encoding=" in args:
                continue
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}")

    assert not offenders, (
        "以下 subprocess 调用使用文本模式却未指定 encoding，"
        "在 GBK 等非 UTF-8 locale 下会因解码失败把 stdout 置为 None：\n  "
        + "\n  ".join(offenders)
    )


def test_guard_actually_inspects_some_calls():
    """守护自身不能因为正则失效而空转通过。"""
    total = 0
    for path in _tracked_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in CALL.finditer(text):
            args = _call_args(text, match.end() - 1)
            if "text=True" in args:
                total += 1
    assert total > 0
