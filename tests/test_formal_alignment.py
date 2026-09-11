"""实现 ↔ TLA+ 规范对齐测试（路径 2，v0.58.0）。

分三层验证：

1. **规范自身**：``.tla`` / ``.cfg`` 存在、模块名匹配、所有定义的不变量都真的
   配进了 ``INVARIANTS``（定义了却不检查 = 没有保护）；
2. **门卫对齐**：规范里的「阶段门卫」表与实现侧 ``DefenseLine.trigger`` 完全一致；
3. **运行时对齐**：真实 ``AntiShortcutSkill`` 的行为符合规范里的推进关系——
   不允许跳跃阶段、``4 -> 6`` 捷径要求「测试新鲜且全绿」、交付即终态。

TLC 模型检查本身在 CI 的 ``formal-invariants`` job 里执行（需要 JDK + tla2tools.jar）；
本地没有 TLC 时这些测试仍然全部有效。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig
from anti_shortcut.defense import BUILTIN_DEFENSE_LINES
from anti_shortcut.formal import (
    ALLOWED_TRANSITIONS,
    CONFIG_FILE,
    DELIVERY_STAGE,
    FORMAL_DIR,
    MAX_STAGE,
    SPEC_FILE,
    SPEC_STAGE_GATES,
    alignment_report,
    configured_invariants,
    declared_invariants,
    spec_text,
    unchecked_invariants,
)

# ---------- 1. 规范自身 ----------


def test_spec_and_config_files_exist():
    assert SPEC_FILE.is_file() and CONFIG_FILE.is_file()
    assert SPEC_FILE.name == "PhaseBarrier.tla"
    text = spec_text()
    assert text.startswith("---- MODULE PhaseBarrier ----")
    assert text.rstrip().endswith("====")
    assert FORMAL_DIR.is_dir()


def test_spec_declares_the_strict_five_line_configuration():
    text = spec_text()
    assert "ASSUME MaxStage = 6" in text
    # 五道防线的门卫组合必须写在规范里
    assert "defense[1] /\\ defense[2] /\\ defense[3]" in text
    assert "defense[4] /\\ defense[5]" in text


def test_every_declared_invariant_is_checked_by_tlc():
    declared = declared_invariants()
    assert len(declared) >= 8, declared
    assert unchecked_invariants() == [], "定义了但没进 .cfg 的不变量等于没有检查"


def test_config_pins_constants_and_disables_deadlock_check():
    text = CONFIG_FILE.read_text(encoding="utf-8")
    assert "SPECIFICATION Spec" in text
    assert "MaxStage = 6" in text
    assert "CHECK_DEADLOCK FALSE" in text, "交付终态无可用动作，必须关掉死锁检查"
    assert configured_invariants() == [
        "INV_TypeOK",
        "INV_DeliveryNeedsFreshTest",
        "INV_CodeOnlyAfterImplementation",
        "INV_DeliveryNeedsBehaviorAudit",
        "INV_StageGates",
        "INV_DeliveryNeedsHumanReview",
        "INV_StageBound",
        "INV_DeliveredIsFinal",
        "INV_TestedVersionBound",
        "INV_TestPassedImpliesRun",
    ]


# ---------- 2. 门卫对齐 ----------


def test_spec_stage_gates_match_implementation_triggers():
    impl_gates: dict[str, set[str]] = {}
    for line in BUILTIN_DEFENSE_LINES:
        for combo in line.trigger:
            impl_gates.setdefault(f"{combo[0]}->{combo[1]}", set()).add(line.name)
    spec_gates = {f"{k[0]}->{k[1]}": set(v) for k, v in SPEC_STAGE_GATES.items()}
    assert impl_gates == spec_gates, (
        "实现侧防线触发组合与 TLA+ 规范不一致——改了任一侧都必须同步另一侧"
    )


def test_alignment_report_is_json_serialisable():
    report = alignment_report()
    payload = json.dumps(report, ensure_ascii=False)
    assert "1->2" in payload and report["unchecked_invariants"] == []
    assert report["declared_invariants"] == declared_invariants()


def test_max_stage_and_delivery_stage_constants():
    assert MAX_STAGE == 6 and DELIVERY_STAGE == 6
    assert (4, 6) in ALLOWED_TRANSITIONS, "「测试通过跳过修复」必须是合法推进"
    assert (1, 3) not in ALLOWED_TRANSITIONS


# ---------- 3. 运行时对齐 ----------


def _spec_text() -> str:
    return """# 斐波那契函数 Spec

## 需求分析
需要一个函数 fib(n)，计算斐波那契数列第 n 项。F(0)=0, F(1)=1。

## 设计方案
采用迭代法，滚动维护前两项，时间复杂度 O(n)。

## 接口定义
def fib(n: int) -> int
"""


GOOD_TESTS = '''"""测试用例"""
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55
'''

GOOD_IMPL = '''def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''


def _tools(ws: Path) -> dict:
    def write_file(path, content):
        target = ws / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True}

    def execute_command(command):
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ws,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        return {
            "exit_code": proc.returncode,
            "output": (proc.stdout or "") + (proc.stderr or ""),
        }

    return {"write_file": write_file, "execute_command": execute_command}


def _new_skill(tmp_path: Path, **cfg_kwargs) -> AntiShortcutSkill:
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths=.\n", encoding="utf-8")
    return AntiShortcutSkill(
        tmp_path, config=GateConfig(**cfg_kwargs), user_request="实现 fib 函数"
    )


def test_no_skip_transitions_except_documented_shortcut(tmp_path):
    """INV-6：只有 ``from -> from+1``（外加 4 -> 6 捷径）能成功推进。

    状态机 bootstrap 时会把阶段 0（需求接收）记入历史并进入阶段 1，因此起点是 1。
    """
    skill = _new_skill(tmp_path)
    try:
        assert skill.current_stage == 1, "bootstrap 后应处于 Spec 设计阶段"
        for target in range(0, MAX_STAGE + 2):
            result = skill.advance_stage(target)
            if (1, target) in ALLOWED_TRANSITIONS:
                # 合法推进：可以因缺证据失败，但绝不能以「跳跃阶段」为由拒绝
                assert "跳跃阶段" not in result.get("error", ""), result
                assert skill.current_stage == 1
            else:
                assert not result["success"], f"阶段 1 -> {target} 必须被拒绝"
                assert "跳跃阶段" in result["error"], result
    finally:
        skill.close()


def test_gate_1_to_2_requires_defense_one(tmp_path):
    """INV-4：防线 1 未通过（缺少需求模板）时不可能进入阶段 2。"""
    cfg = GateConfig(defense={"requirement_template": {"enabled": True, "strict": True}})
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths=.\n", encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, config=cfg, user_request="实现 fib 函数")
    try:
        tools = skill.install(_tools(tmp_path))
        tools["write_file"]("spec.md", _spec_text())
        result = skill.advance_stage(2)
        assert not result["success"] and skill.current_stage == 1
        assert "防线 1" in result["error"]
        # 补齐模板后同一推进必须成功（防线 1 是唯一阻塞点）
        (tmp_path / "requirement.yaml").write_text(
            json.dumps(
                {
                    "goal": "实现斐波那契函数",
                    "forbidden": ["不允许递归导致栈溢出"],
                    "interfaces": ["def fib(n: int) -> int", "def fib_fast(n: int) -> int"],
                    "acceptance": ["fib(10) == 55", "负数抛 ValueError"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert skill.advance_stage(2)["success"] and skill.current_stage == 2
    finally:
        skill.close()


def _drive_to_test_run(skill: AntiShortcutSkill, ws: Path) -> dict:
    tools = skill.install(_tools(ws))
    tools["write_file"]("spec.md", _spec_text())
    assert skill.advance_stage(2)["success"]
    tools["write_file"]("tests/test_fib.py", GOOD_TESTS)
    assert skill.advance_stage(3)["success"]
    tools["write_file"]("fib.py", GOOD_IMPL)
    assert skill.advance_stage(4)["success"]
    tools["execute_command"]("python -m pytest tests -q -p no:cacheprovider")
    return tools


def test_delivery_shortcut_requires_fresh_green_test(tmp_path):
    """INV-1：``4 -> 6`` 只在「测试通过且测试后没改过代码」时成立。"""
    skill = _new_skill(tmp_path)
    try:
        tools = _drive_to_test_run(skill, tmp_path)
        # 测试后改了实现 -> 不能直接交付，必须进入修复阶段
        tools["write_file"]("fib.py", GOOD_IMPL + "\n# tweak\n")
        result = skill.advance_stage(5)
        assert result["success"] and skill.current_stage == 5, result
        # 重跑测试让回归重新变绿 -> 才能交付
        tools["execute_command"]("python -m pytest tests -q -p no:cacheprovider")
        result = skill.advance_stage(6)
        assert result["success"] and skill.current_stage == DELIVERY_STAGE, result
        assert skill.is_complete
    finally:
        skill.close()


def test_delivery_is_terminal(tmp_path):
    """INV-7：交付后状态机不再接受任何推进。"""
    skill = _new_skill(tmp_path)
    try:
        _drive_to_test_run(skill, tmp_path)
        assert skill.advance_stage(5)["success"]
        assert skill.current_stage == DELIVERY_STAGE
        after = skill.advance_stage(6)
        assert not after["success"] and "已完成" in after["error"]
    finally:
        skill.close()


def test_stage_bound_never_exceeded(tmp_path):
    """INV-6：任何推进尝试都不能让阶段超过 MaxStage。"""
    skill = _new_skill(tmp_path)
    try:
        for target in range(0, MAX_STAGE + 3):
            skill.advance_stage(target)
            assert 0 <= skill.current_stage <= MAX_STAGE
    finally:
        skill.close()


# ---------- 4. 可选：真实 TLC 模型检查 ----------


def _find_tla_jar() -> str | None:
    """定位 tla2tools.jar：``PHASE_BARRIER_TLC_JAR`` 优先，其次 PATH。"""
    import os
    import shutil as _shutil

    env = os.environ.get("PHASE_BARRIER_TLC_JAR")
    if env and Path(env).is_file():
        return env
    return _shutil.which("tla2tools.jar")


def test_tlc_model_check_passes_when_jar_is_available(tmp_path):
    """用真实 TLC 跑一遍规范（CI ``formal-invariants`` job 会下载 jar 并设置

    ``PHASE_BARRIER_TLC_JAR``）。本机没有 jar / JDK 时跳过，而不是假装通过。
    """
    import os
    import shutil as _shutil

    jar = _find_tla_jar()
    java = _shutil.which("java")
    if jar is None or java is None:
        pytest.skip("本机没有 tla2tools.jar 或 java（CI formal-invariants job 会跑）")

    work = tmp_path / "tlc"
    work.mkdir()
    _shutil.copy(SPEC_FILE, work / SPEC_FILE.name)
    _shutil.copy(CONFIG_FILE, work / CONFIG_FILE.name)
    proc = subprocess.run(
        [java, "-jar", str(jar), "-config", CONFIG_FILE.name, SPEC_FILE.name],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={**os.environ},
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, output[-4000:]
    assert "No error has been found" in output, output[-2000:]
    assert "distinct states found" in output
