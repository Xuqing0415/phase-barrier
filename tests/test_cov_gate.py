"""覆盖率门禁测试（v0.7.0）：coverage_threshold 提取与阶段 4/5 校验。"""
from pathlib import Path

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig
from anti_shortcut.interceptors import summarize_test_output
from anti_shortcut.state import StateManager
from anti_shortcut.validators import validate_retest, validate_test_run
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


def make_state(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "state.json", user_request="r")


# ---------- 覆盖率提取 ----------

def test_summarize_extracts_go_coverage():
    tr = summarize_test_output("ok  pkg/fib  coverage: 89.1% of statements", 0)
    assert tr["coverage"] == 89.1


def test_summarize_extracts_pytest_cov_total():
    tr = summarize_test_output(
        "Name    Stmts   Miss  Cover\n"
        "fib.py       5      0   100%\n"
        "TOTAL        5      0   100%\n",
        0,
    )
    assert tr["coverage"] == 100.0


def test_summarize_extracts_istanbul_table():
    tr = summarize_test_output(
        "File      | % Stmts | % Branch | % Funcs | % Lines |\n"
        "----------|---------|----------|---------|---------|\n"
        "All files |     100 |      100 |     100 |     100 |\n",
        0,
    )
    assert tr["coverage"] == 100.0


def test_summarize_no_coverage_is_none():
    tr = summarize_test_output("3 passed", 0)
    assert tr["coverage"] is None


# ---------- validate_test_run ----------

def test_test_run_cov_threshold_missing_report(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "summary": "3 passed"})
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert not ok and "覆盖率" in msg


def test_test_run_cov_below_threshold(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 60.0})
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert not ok and "覆盖率不足" in msg and "60.0" in msg


def test_test_run_cov_meets_threshold(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 90.0})
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert ok


def test_test_run_no_threshold_no_coverage_ok(tmp_path):
    cfg = GateConfig()
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True})
    ok, _, _ = validate_test_run(tmp_path, cfg, s)
    assert ok


# ---------- validate_retest ----------

def test_retest_cov_threshold_blocks(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_source_change("fib.py")
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 50.0})
    ok, msg, _ = validate_retest(tmp_path, cfg, s)
    assert not ok and "覆盖率不足" in msg


def test_retest_cov_threshold_ok(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_source_change("fib.py")
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 95.0})
    ok, msg, ev = validate_retest(tmp_path, cfg, s)
    assert ok
    assert ev["coverage"] == 95.0


# ---------- 端到端（Skill 全流程） ----------

def test_skill_cov_gate_full_flow(tmp_path, fake_tools):
    """验收：配置 coverage_threshold 后，覆盖率不足被阶段 4 拒绝。"""
    skill = AntiShortcutSkill(
        tmp_path, config={"coverage_threshold": 80}, user_request=USER_REQUEST
    )
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", GOOD_IMPL)
    assert tools["advance_stage"](4)["success"]

    # 覆盖率不足 -> 阶段 4 校验拒绝
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "coverage": 70.0})
    r = tools["advance_stage"](5)
    assert not r["success"] and "覆盖率不足" in r["error"]

    # 覆盖率达标 -> 通过并直接交付（跳过修复）
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "coverage": 90.0})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete


def test_skill_cov_gate_retest_path(tmp_path, fake_tools):
    """测试未通过进入阶段 5；修复后重测仍要求覆盖率达标。"""
    skill = AntiShortcutSkill(
        tmp_path, config={"coverage_threshold": 80}, user_request=USER_REQUEST
    )
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    tools["advance_stage"](2)
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    tools["advance_stage"](3)
    tools["write_file"]("fib.py", GOOD_IMPL)
    tools["advance_stage"](4)

    # 第一次测试失败 -> 进入阶段 5
    skill.state.mark_test_run({"exit_code": 1, "passed": False, "coverage": 80.0})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 5

    # 修复后重测但覆盖率不足 -> 拒绝
    skill.state.mark_source_change("fib.py")
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "coverage": 70.0})
    r = tools["advance_stage"](6)
    assert not r["success"] and "覆盖率不足" in r["error"]

    # 修复后重测且覆盖率达标 -> 通过并交付
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "coverage": 90.0})
    r = tools["advance_stage"](6)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete
