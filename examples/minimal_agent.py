"""最小可运行的模拟 Agent 集成示例。

展示一个“偷懒”的 Agent 循环如何被阶段门禁拦下，以及按 SOP 推进的完整流程。

运行：
    python examples/minimal_agent.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut import AntiShortcutSkill  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent / "minimal_workspace"

SPEC = """# 斐波那契数列函数

## 需求分析
实现计算斐波那契数列第 n 项的函数：输入非负整数 n，输出第 n 项数值。需处理 n=0/1 边界并拒绝负数输入。

## 设计方案
递归 + 记忆化（字典缓存），时间复杂度 O(n)、空间复杂度 O(n)；n>1000 时抛出 ValueError 防止栈溢出。

## 接口定义
def fib(n: int) -> int：n<0 抛 ValueError；n=0 返回 0，n=1 返回 1，其余返回 fib(n-1)+fib(n-2)。
"""

TESTS = """\
from fib import fib

def test_fib_small():
    assert fib(0) == 0
    assert fib(1) == 1
    assert fib(2) == 1
    assert fib(3) == 2

def test_fib_known():
    assert fib(10) == 55
    assert fib(20) == 6765
"""

IMPL = """\
def fib(n: int) -> int:
    if n < 0:
        raise ValueError("n must be non-negative")
    cache: dict[int, int] = {0: 0, 1: 1}

    def _fib(k: int) -> int:
        if k not in cache:
            cache[k] = _fib(k - 1) + _fib(k - 2)
        return cache[k]

    return _fib(n)
"""


def make_tools(ws: Path) -> dict:
    """模拟 Agent 的底层工具：真实文件写入 + 真实 shell 执行（返回 dict 约定）。"""

    def write_file(path, content):
        target = ws / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def execute_command(command):
        proc = subprocess.run(
            command,
            shell=True,
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 兼容 Windows 控制台使用 GBK 等非 UTF-8 编码输出
        )
        return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}

    return {"write_file": write_file, "execute_command": execute_command}


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True)

    skill = AntiShortcutSkill(workspace=WORKSPACE, user_request="实现一个计算斐波那契数列的函数")
    tools = skill.install(make_tools(WORKSPACE))

    def call(name, *args):
        arg_str = ", ".join(str(a)[:40] for a in args)
        try:
            tools[name](*args)
            print(f"[OK]      {name}({arg_str})")
        except PermissionError as exc:
            print(f"[BLOCKED] {name}({arg_str})\n          -> {exc}")

    print("=== 阶段 1（Spec 设计）===")
    call("write_file", "fib.py", IMPL)          # 偷懒：跳过 spec/测试直接写实现 -> 拦截
    call("execute_command", "pytest -q")        # 偷懒：没写实现就想跑测试 -> 拦截

    print("\n=== 按 SOP 推进 ===")
    call("write_file", "spec.md", SPEC)
    print("[advance] ->", tools["advance_stage"](2)["message"])

    call("write_file", "test_fib.py", TESTS)
    print("[advance] ->", tools["advance_stage"](3)["message"])

    call("write_file", "fib.py", IMPL)
    print("[advance] ->", tools["advance_stage"](4)["message"])

    call("execute_command", "pytest -q")
    print("[advance] ->", tools["advance_stage"](5)["message"])

    print(f"\n最终阶段: {skill.current_stage}（{skill.stage_name}），完成 = {skill.is_complete}")


if __name__ == "__main__":
    main()
