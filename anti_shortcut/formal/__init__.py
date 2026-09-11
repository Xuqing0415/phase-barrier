"""形式化不变量（路径 2，v0.58.0）。

状态机核心不变量用 TLA+ 表达并由 TLC 模型检查（``PhaseBarrier.tla`` /
``PhaseBarrier.cfg``）。本模块提供**实现 ↔ 规范对齐**所需的机器可读元数据与
解析工具，供 ``tests/test_formal_alignment.py`` 与 CI ``tla-check`` job 使用：

- :data:`SPEC_STAGE_GATES`：规范侧声明的「阶段门卫」——某个推进组合上必须
  通过的防线。实现侧对应 ``DefenseLine.trigger``；
- :data:`ALLOWED_TRANSITIONS`：允许的阶段推进（其余必须被拒绝）；
- :func:`declared_invariants` / :func:`configured_invariants`：分别从 ``.tla``
  与 ``.cfg`` 里读出「定义了哪些不变量」「TLC 实际检查了哪些不变量」，
  防止有人改了规范却漏配 ``.cfg``（不变量定义了但不检查 = 没有保护）。

不变量与实现的对应关系（详见 ``docs/formal-invariants.md``）：

===================  ==============================================
不变量                实现侧的保证点
===================  ==============================================
INV-1 交付需新鲜测试    ``validators.validate_retest`` / 阶段 4 -> 6 分支
INV-2 改码不早于实现    ``skill.check_write_permission`` 阶段门禁
INV-3/5 交付需防线 4/5  ``defense.behavior_audit`` / ``human_review`` 触发点
INV-4 防线 1/2/3 守 1→2 ``defense.requirement_template`` / ``dual_review`` / ``formal_check``
INV-6 阶段不越界        ``skill.advance_stage`` 的「只能 +1」约束
INV-7 交付即终态        ``skill.is_complete``
INV-8 时间戳单调        ``state.mark_source_change`` / ``mark_test_run``
===================  ==============================================
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "FORMAL_DIR",
    "SPEC_FILE",
    "CONFIG_FILE",
    "DELIVERY_STAGE",
    "MAX_STAGE",
    "ALLOWED_TRANSITIONS",
    "SPEC_STAGE_GATES",
    "spec_text",
    "config_text",
    "declared_invariants",
    "configured_invariants",
    "unchecked_invariants",
    "alignment_report",
]

FORMAL_DIR = Path(__file__).resolve().parent
SPEC_FILE = FORMAL_DIR / "PhaseBarrier.tla"
CONFIG_FILE = FORMAL_DIR / "PhaseBarrier.cfg"

MAX_STAGE = 6
DELIVERY_STAGE = 6

#: 允许的阶段推进。其余组合必须被 ``advance_stage`` 拒绝（不允许跳跃阶段）；
#: ``4 -> 6`` 是「测试全部通过、跳过修复阶段」的合法捷径。
ALLOWED_TRANSITIONS: frozenset[tuple[int, int]] = frozenset(
    {(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (4, 6), (5, 6)}
)

#: 规范侧的阶段门卫：推进组合 -> 该组合必须通过的防线（与实现侧 trigger 对齐）
SPEC_STAGE_GATES: dict[tuple[int, int], tuple[str, ...]] = {
    (1, 2): ("requirement_template", "dual_review", "formal_check"),
    (4, 6): ("behavior_audit", "human_review"),
    (5, 6): ("behavior_audit", "human_review"),
}


def spec_text() -> str:
    """返回 TLA+ 规范文本。"""
    return SPEC_FILE.read_text(encoding="utf-8")


def config_text() -> str:
    """返回 TLC 配置文本。"""
    return CONFIG_FILE.read_text(encoding="utf-8")


def declared_invariants() -> list[str]:
    """``.tla`` 里定义的 ``INV_*`` 不变量名（按字母序）。"""
    return sorted(set(re.findall(r"(?m)^(INV_[A-Za-z0-9_]+)\s*==", spec_text())))


def configured_invariants() -> list[str]:
    """``.cfg`` 的 ``INVARIANTS`` 段里列出、会被 TLC 实际检查的不变量名。"""
    text = config_text()
    match = re.search(r"(?m)^INVARIANTS\s*$", text)
    if not match:
        return []
    names: list[str] = []
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("\\*") and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
            names.append(stripped)
            continue
        break
    return names


def unchecked_invariants() -> list[str]:
    """定义了但没配进 ``.cfg`` 的不变量（应为空，否则等于没有检查）。"""
    return sorted(set(declared_invariants()) - set(configured_invariants()))


def alignment_report() -> dict[str, object]:
    """汇总实现与规范的对齐情况（供测试与 CI 输出）。"""
    from ..defense import BUILTIN_DEFENSE_LINES

    impl_gates: dict[str, list[str]] = {}
    for line in BUILTIN_DEFENSE_LINES:
        for combo in line.trigger:
            impl_gates.setdefault(f"{combo[0]}->{combo[1]}", []).append(line.name)
    return {
        # 键统一用 "from->to" 字符串，便于直接序列化成 JSON 报告
        "impl_stage_gates": {k: sorted(v) for k, v in sorted(impl_gates.items())},
        "spec_stage_gates": {f"{k[0]}->{k[1]}": list(v) for k, v in sorted(SPEC_STAGE_GATES.items())},
        "allowed_transitions": sorted(ALLOWED_TRANSITIONS),
        "declared_invariants": declared_invariants(),
        "configured_invariants": configured_invariants(),
        "unchecked_invariants": unchecked_invariants(),
    }
