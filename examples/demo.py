# 模拟 Agent 演示：完整走一遍反捷径校验 Skill 的阶段门禁流程
#
# 场景：用户需求"实现一个计算斐波那契数列的函数"。
# 展示：
#   1) 违规尝试（跳步写实现 / 提前跑测试）如何被工具拦截器拒绝
#   2) 空壳测试文件如何被 AST 校验拒绝
#   3) 测试失败进入修复阶段，修复后未重测被拒绝，重测通过后进入交付
#
# 运行：python examples/demo.py

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut import AntiShortcutSkill  # noqa: E402

BASE_WORKSPACE = Path(__file__).resolve().parent / "demo_workspace"


def pick_workspace() -> Path:
    """优先使用 demo_workspace；若其残留目录被沙箱/系统锁定无法清理，
    则回退到带时间戳的工作区，保证演示总能从干净的阶段 0 开始。"""
    import datetime

    ws = BASE_WORKSPACE
    try:
        _clear_readonly(ws)
        if ws.exists():
            shutil.rmtree(ws)
        return ws
    except OSError:
        suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = BASE_WORKSPACE.parent / f"demo_workspace_{suffix}"
        print(f"[demo] 无法清理 {ws}，回退到 {alt}")
        return alt

USER_REQUEST = "实现一个计算斐波那契数列的函数 fib(n)，返回第 n 项（n >= 0，F(0)=0, F(1)=1）。"

SPEC = """# 斐波那契数列函数 Spec

## 需求分析
用户需要一个函数 fib(n)，计算斐波那契数列第 n 项。
- F(0) = 0, F(1) = 1, F(n) = F(n-1) + F(n-2)
- n 为非负整数；n < 0 或非整数时抛出 ValueError。
- 复杂度要求：时间复杂度 O(n)，避免指数级递归。

## 设计方案
采用迭代法，以两个变量滚动维护前两项，避免递归栈与重复计算。
函数签名：def fib(n: int) -> int

## 接口定义
- 输入：n: int，非负整数
- 输出：int，第 n 项斐波那契数
- 异常：n < 0 或类型错误时抛 ValueError
- 边界：fib(0) == 0, fib(1) == 1, fib(10) == 55
"""

GOOD_TESTS = '''"""test_fib.py - 斐波那契函数测试用例"""
import pytest
from fib import fib


def test_fib_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_fib_small_values():
    assert fib(2) == 1
    assert fib(3) == 2
    assert fib(4) == 3
    assert fib(5) == 5


def test_fib_known_value():
    assert fib(10) == 55


def test_fib_rejects_negative():
    with pytest.raises(ValueError):
        fib(-1)
'''

EMPTY_TESTS = '''def test_nothing():
    pass
'''

BUGGY_IMPL = '''def fib(n):
    """返回斐波那契数列第 n 项。"""
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return a  # bug：应返回 b
'''

FIXED_IMPL = '''def fib(n):
    """返回斐波那契数列第 n 项。"""
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''


def make_tools(workspace: Path):
    """模拟 Agent 的底层工具（真实文件系统 + 真实 shell）。"""

    def write_file(path, content):
        p = workspace / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p)}

    def execute_command(command):
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=workspace,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {"exit_code": proc.returncode, "output": output}

    return {"write_file": write_file, "execute_command": execute_command}


def _clear_readonly(path: Path) -> None:
    """清除目录树内的只读属性（Windows），避免历史遗留的只读文件阻塞清理。"""
    if not path.exists():
        return
    for p in path.rglob("*"):
        try:
            p.chmod(0o644)
        except OSError:
            pass
    try:
        path.chmod(0o755)
    except OSError:
        pass


def main() -> int:
    ws = pick_workspace()
    ws.mkdir(parents=True)
    # 隔离 pytest rootdir，避免上溯到本仓库根目录的 pyproject.toml
    (ws / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")

    print("=" * 72)
    print("反捷径校验 Skill 演示：需求 -> spec -> 测试 -> 实现 -> 测试 -> 修复 -> 交付")
    print("=" * 72)

    tools = make_tools(ws)
    skill = AntiShortcutSkill(ws, user_request=USER_REQUEST)
    tools = skill.install(tools)
    write_file = tools["write_file"]
    execute_command = tools["execute_command"]
    advance_stage = tools["advance_stage"]

    def act(label, fn):
        try:
            result = fn()
            print(f"\n[Agent] {label}")
            if isinstance(result, dict) and "success" in result:
                if result["success"]:
                    print(f"   -> OK: {result['message']}")
                else:
                    print(f"   -> REJECTED: {result['error']}")
            else:
                print(f"   -> OK: {result}")
        except PermissionError as exc:
            print(f"\n[Agent] {label}")
            print(f"   -> BLOCKED: {exc}")
            return None
        return result

    print(f"\n[Skill] 阶段 0（需求接收）完成，当前阶段 1（{skill.stage_name}）")
    print(f"[Skill] 状态文件：{ws / '.agent_gate' / 'state.json'}")

    # ---- 违规尝试 1：未写 spec 就写实现代码 ----
    act("尝试跳过 spec 直接编写实现代码 fib.py", lambda: write_file("fib.py", BUGGY_IMPL))

    # ---- 违规尝试 2：未写实现就运行测试 ----
    act("尝试提前运行 pytest", lambda: execute_command("python -m pytest test_fib.py"))

    # ---- 阶段 1：Spec 设计 ----
    act("编写 spec.md", lambda: write_file("spec.md", SPEC))
    act("推进阶段 1 -> 2", lambda: advance_stage(2))

    # ---- 阶段 2：测试用例 ----
    act("先写一个空壳测试文件（无断言的测试函数）", lambda: write_file("test_fib.py", EMPTY_TESTS))
    act("尝试推进阶段 2（应被 AST 校验拒绝）", lambda: advance_stage(3))
    act("改写为完整测试用例", lambda: write_file("test_fib.py", GOOD_TESTS))
    act("推进阶段 2 -> 3", lambda: advance_stage(3))

    # ---- 阶段 3：实现 ----
    act("编写实现代码（故意留 bug）", lambda: write_file("fib.py", BUGGY_IMPL))
    act("推进阶段 3 -> 4", lambda: advance_stage(4))

    # ---- 阶段 4：运行测试（首次失败）----
    act("运行 pytest（首次，预期失败）", lambda: execute_command("python -m pytest test_fib.py -q -p no:cacheprovider"))
    act("推进阶段 4（测试未通过，应进入阶段 5 修复）", lambda: advance_stage(5))

    # ---- 阶段 5：修复与回归 ----
    act("修复实现代码", lambda: write_file("fib.py", FIXED_IMPL))
    act("未重新运行测试就尝试推进（应被拒绝）", lambda: advance_stage(6))
    act("重新运行 pytest（预期通过）", lambda: execute_command("python -m pytest test_fib.py -q -p no:cacheprovider"))
    act("推进阶段 5 -> 6（交付）", lambda: advance_stage(6))

    # ---- 结果 ----
    print("\n" + "=" * 72)
    print(f"[Skill] 任务{'完成' if skill.is_complete else '未完成'}！最终阶段：{skill.current_stage}（{skill.stage_name}）")
    print("=" * 72)

    snapshot = skill.state.snapshot()
    print("\n[状态机快照]")
    print(f"  current_stage    : {snapshot['current_stage']}")
    print(f"  completed_stages : {snapshot['completed_stages']}")
    for item in snapshot["stage_history"]:
        print(f"  - 阶段 {item['stage']} {item['name']} @ {item['timestamp']}")
    last_run = snapshot["evidence"]["last_test_run"]
    print(f"[最近一次测试] exit_code={last_run.get('exit_code')} passed={last_run.get('passed')}")
    print(f"  summary: {last_run.get('summary')}")

    audit = ws / ".agent_gate" / "audit.log"
    print(f"\n[审计日志] {audit}（{audit.stat().st_size} bytes）")
    print("  行数:", sum(1 for _ in audit.open(encoding="utf-8")))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
