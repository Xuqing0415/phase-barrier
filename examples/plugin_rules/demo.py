"""phase-barrier 插件示例：进程内注册 / 入口点加载两种接入方式。

运行：
    python examples/plugin_rules/demo.py                    # 进程内注册（零安装）
    pip install -e examples/plugin_rules                    # 安装示例插件包
    python examples/plugin_rules/demo.py --via-entry-point  # 经入口点加载
"""
import argparse
import shutil
import sys
from pathlib import Path

from anti_shortcut import AntiShortcutSkill, register_rule, register_validator
from anti_shortcut.validators import get_validator

sys.path.insert(0, str(Path(__file__).parent))
import phase_barrier_plugin as plugin

SPEC = """# 示例 Spec

## 需求分析
需要一个 add(a, b) 函数，返回两数之和。

## 设计方案
直接返回 a + b，无副作用。

## 接口定义
def add(a: int, b: int) -> int
"""


def _fresh_workspace() -> Path:
    base = Path(__file__).parent
    ws = base / "_demo_ws"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir()
    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    return ws


def main() -> int:
    parser = argparse.ArgumentParser(description="phase-barrier 插件示例")
    parser.add_argument(
        "--via-entry-point",
        action="store_true",
        help="通过入口点加载（需先 pip install -e 本目录）",
    )
    args = parser.parse_args()

    if args.via_entry_point:
        validator = get_validator(1)
        assert validator is not None and validator.__name__ == "require_design_review", (
            "入口点插件未加载：请先运行 pip install -e examples/plugin_rules"
        )
        print("入口点校验器已加载：require_design_review")
    else:
        register_validator(1, plugin.require_design_review)
        register_rule("deny_vendor", plugin.deny_vendor_writes)
        print("进程内已注册：register_validator / register_rule")

    ws = _fresh_workspace()
    try:
        skill = AntiShortcutSkill(ws, user_request="实现 add(a, b)")

        # 1) 自定义校验器：缺少 design-review.md 时拒绝推进到阶段 2
        r = skill.advance_stage(2)
        print(f"advance(2) 无 design-review -> success={r['success']}: {r['error']}")
        assert r["success"] is False and "design-review.md" in r["error"]

        (ws / "design-review.md").write_text("reviewed\n", encoding="utf-8")
        r = skill.advance_stage(2)
        print(f"advance(2) 有 design-review -> success={r['success']}: {r['message']}")
        assert r["success"] is True

        # 2) 自定义拦截规则：禁止写入 vendor/
        try:
            skill.check_write_permission("vendor/locked.txt")
        except PermissionError as exc:
            print(f"vendor/ 写入被拦截 -> {exc}")
        else:
            raise SystemExit("vendor/ 写入应被自定义规则拦截")

        print("OK: 插件示例运行完成")
        return 0
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
