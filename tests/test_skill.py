"""Skill 端到端测试：阶段门禁、工具拦截、完整流程。"""
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from conftest import BUGGY_IMPL, EMPTY_TESTS, GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


def make_skill(tmp_path: Path, **kwargs) -> AntiShortcutSkill:
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(tmp_path, user_request=USER_REQUEST, **kwargs)


# ---------- 初始化 ----------

def test_init_state(tmp_path):
    skill = make_skill(tmp_path)
    assert skill.current_stage == 1
    assert skill.stage_name == "Spec \u8bbe\u8ba1"
    assert not skill.is_complete
    gate = tmp_path / ".agent_gate"
    assert (gate / "state.json").exists()
    assert (gate / "audit.log").exists()
    assert skill.state.get_evidence("user_request") == USER_REQUEST


def test_install_wraps_tools(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    orig_write = fake_tools["write_file"]
    orig_exec = fake_tools["execute_command"]
    tools = skill.install(fake_tools)
    assert "advance_stage" in tools
    assert tools["write_file"] is not orig_write
    assert tools["execute_command"] is not orig_exec


# ---------- 工具拦截 ----------

def test_source_write_blocked_before_tests(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="\u6d4b\u8bd5\u7528\u4f8b"):
        tools["write_file"]("fib.py", GOOD_IMPL)


def test_test_write_blocked_before_spec(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    # \u767d\u7bb1\uff1a\u628a\u9636\u6bb5\u641e\u5230 0\uff0c\u6a21\u62df spec \u672a\u5b8c\u6210
    skill.state._data["current_stage"] = 0
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="spec"):
        tools["write_file"]("test_fib.py", GOOD_TESTS)


def test_gate_dir_write_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="agent_gate"):
        tools["write_file"](".agent_gate/state.json", "{}")


def test_test_command_blocked_before_impl(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="\u5b9e\u73b0\u4ee3\u7801"):
        tools["execute_command"]("python -m pytest test_fib.py")


def test_gate_dir_command_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="agent_gate"):
        tools["execute_command"]("rm -rf .agent_gate")


def test_shell_redirect_source_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError):
        tools["execute_command"]("echo x > fib.py")


def test_sed_source_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError):
        tools["execute_command"]("sed -i s/a/b/ fib.py")


def test_read_commands_allowed(tmp_path, fake_tools):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    result = tools["execute_command"]("dir")
    assert result["exit_code"] == 0


def test_jump_rejected(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    result = tools["advance_stage"](3)
    assert result["success"] is False
    assert "\u8df3\u8dc3\u9636\u6bb5" in result["error"]


def test_advance_after_complete_rejected(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    _run_full_flow(tmp_path, tools, GOOD_IMPL)
    result = tools["advance_stage"](7)
    assert result["success"] is False
    assert "\u5df2\u5b8c\u6210" in result["error"]


# ---------- 完整流程：测试失败 -> 修复 -> 回归 ----------

def test_full_flow_with_fix(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)

    # spec
    tools["write_file"]("spec.md", SPEC)
    r = tools["advance_stage"](2)
    assert r["success"] and r["stage"] == 2

    # \u7a7a\u58f3\u6d4b\u8bd5\u88ab\u62d2
    tools["write_file"]("test_fib.py", EMPTY_TESTS)
    r = tools["advance_stage"](3)
    assert r["success"] is False

    # \u5b8c\u6574\u6d4b\u8bd5
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    r = tools["advance_stage"](3)
    assert r["success"] and r["stage"] == 3

    # bug \u5b9e\u73b0
    tools["write_file"]("fib.py", BUGGY_IMPL)
    r = tools["advance_stage"](4)
    assert r["success"] and r["stage"] == 4

    # \u6d4b\u8bd5\u5931\u8d25 -> \u8fdb\u5165\u4fee\u590d\u9636\u6bb5 5
    r = tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert r["exit_code"] != 0
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 5
    assert skill.state.get_evidence("last_test_run")["passed"] is False

    # \u4fee\u590d\u540e\u672a\u91cd\u6d4b -> \u62d2\u7edd
    tools["write_file"]("fib.py", GOOD_IMPL)
    r = tools["advance_stage"](6)
    assert r["success"] is False
    assert "\u91cd\u65b0\u8fd0\u884c\u6d4b\u8bd5" in r["error"]

    # \u91cd\u6d4b\u901a\u8fc7 -> \u4ea4\u4ed8
    r = tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert r["exit_code"] == 0
    r = tools["advance_stage"](6)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete
    assert skill.state.completed_stages == [0, 1, 2, 3, 4, 5]


# ---------- \u5b8c\u6574\u6d41\u7a0b\uff1a\u6d4b\u8bd5\u76f4\u63a5\u901a\u8fc7 -> \u8df3\u8fc7\u4fee\u590d ----------

def test_full_flow_pass_skips_fix(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", GOOD_IMPL)
    assert tools["advance_stage"](4)["success"]

    r = tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert r["exit_code"] == 0
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6  # \u76f4\u63a5\u8df3\u5230\u4ea4\u4ed8
    assert "3 passed" in skill.state.get_evidence("last_test_run")["summary"]


# ---------- \u9636\u6bb5 4 \uff1a\u6d4b\u8bd5\u901a\u8fc7\u540e\u53c8\u6539\u4ee3\u7801 -> \u5fc5\u987b\u91cd\u6d4b ----------

def test_stage4_change_after_pass_requires_retest(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", GOOD_IMPL)
    assert tools["advance_stage"](4)["success"]

    assert tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")["exit_code"] == 0
    tools["write_file"]("fib.py", GOOD_IMPL + "\n# comment change\n")
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 5  # \u88ab\u5f15\u5bfc\u56de\u4fee\u590d/\u91cd\u6d4b
    assert "\u91cd\u65b0\u8fd0\u884c\u6d4b\u8bd5" in r["message"] or r["stage"] == 5


# ---------- \u8f85\u52a9 ----------

def _run_full_flow(tmp_path: Path, tools: dict, impl: str):
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", impl)
    assert tools["advance_stage"](4)["success"]
    tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert tools["advance_stage"](5)["success"]


# ---------- v0.3.1 新增：拦截器边界 ----------

def test_shell_dd_write_source_blocked(tmp_path, fake_tools):
    """阶段 1：dd of= 写实现文件应被拦截。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("dd if=/dev/zero of=fib.py bs=1024 count=1")


def test_shell_tee_write_source_blocked(tmp_path, fake_tools):
    """阶段 1：tee 写实现文件应被拦截。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("tee fib.py")


def test_gate_dir_dd_command_blocked(tmp_path, fake_tools):
    """dd 试图覆写状态文件应被门禁目录保护拦截。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="agent_gate"):
        tools["execute_command"]("dd of=.agent_gate/state.json bs=1024")


def test_check_write_permission_special_chars(tmp_path, fake_tools):
    """路径含空格 / 非 ASCII 字符时仍按源代码规则拦截。"""
    skill = make_skill(tmp_path)
    with pytest.raises(PermissionError, match="实现代码"):
        skill.check_write_permission("my fib.py")
    with pytest.raises(PermissionError, match="实现代码"):
        skill.check_write_permission("测试.py")
    skill.check_write_permission("README.md")  # 其他类型默认放行


def test_dd_source_write_allowed_after_impl(tmp_path, fake_tools):
    """阶段 3 之后 dd 写实现文件不再被拦截（check_exec_permission 不抛异常）。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    skill.check_exec_permission("dd if=/dev/zero of=fib.py bs=1024 count=1")
