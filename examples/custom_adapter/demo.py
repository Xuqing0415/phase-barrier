"""自定义语言适配器的最小可运行演示。

流程：写 spec -> 写 .foo 测试 -> 写 .foo 实现 -> 推进阶段 -> 运行 foo test -> 交付。
展示自定义适配器参与文件识别、语法检查、测试校验与测试命令识别。

运行：
    python examples/custom_adapter/demo.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(THIS))  # 让 foo_language 可被配置导入

from anti_shortcut import AntiShortcutSkill  # noqa: E402

WORKSPACE = THIS / "foo_workspace"

SPEC = """# .foo 语言示例

## 需求分析
实现一个 add 函数：输入两个整数，输出它们的和。

## 设计方案
纯函数实现，参数命名 a/b，返回 a+b。

## 接口定义
add(a, b) -> int
"""

TESTS = """\
TEST add_basic()
ASSERT add(1, 2) == 3
TEST add_negative()
ASSERT add(-1, 1) == 0
"""

IMPL = """\
function add(a, b) {
  return a + b;
}
"""


def make_tools(ws: Path) -> dict:
    """模拟 Agent 底层工具；``foo test`` 由桩返回成功，模拟 .foo 测试运行器。"""

    def write_file(path, content):
        target = ws / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def execute_command(command):
        if (command or "").strip().startswith("foo test"):
            return {"exit_code": 0, "output": "2 tests passed"}
        proc = subprocess.run(
            command,
            shell=True,
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}

    return {"write_file": write_file, "execute_command": execute_command}


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True)

    skill = AntiShortcutSkill(
        workspace=WORKSPACE,
        config=str(THIS / "foo_config.yaml"),
        user_request="实现 .foo 语言的 add 函数",
    )
    tools = skill.install(make_tools(WORKSPACE))

    def call(name, *args):
        arg_str = ", ".join(str(a)[:40] for a in args)
        try:
            tools[name](*args)
            print(f"[OK]      {name}({arg_str})")
        except PermissionError as exc:
            print(f"[BLOCKED] {name}({arg_str})\n          -> {exc}")

    print("=== 偷懒尝试（应被拦截）===")
    call("write_file", "add.foo", IMPL)
    call("execute_command", "foo test")

    print("\n=== 按 SOP 推进 ===")
    call("write_file", "spec.md", SPEC)
    print("[advance] ->", tools["advance_stage"](2)["message"])

    call("write_file", "add.test.foo", TESTS)
    print("[advance] ->", tools["advance_stage"](3)["message"])

    call("write_file", "add.foo", IMPL)
    print("[advance] ->", tools["advance_stage"](4)["message"])

    call("execute_command", "foo test")
    print("[advance] ->", tools["advance_stage"](5)["message"])

    print(f"\n最终阶段: {skill.current_stage}（{skill.stage_name}），完成 = {skill.is_complete}")


if __name__ == "__main__":
    main()