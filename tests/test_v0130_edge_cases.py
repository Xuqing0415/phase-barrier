"""v0.13.0 边界补强测试：拦截器边界 + CLI 错误处理。

覆盖：
- 命令注入变体（$() / 反引号 / 管道）仍被识别为测试命令；
- 重定向写路径提取（1>/2>/&>/&>>/>|）；
- 门禁目录 .agent_gate 的所有常见写方法（tee/dd/sed -i/mv/cp/touch/install/rm/echo>）都被拦截；
- 写路径含空格 / Unicode / 括号时文件类型判断正确；
- CLI：损坏状态（含 --json）、缺字段状态、越界阶段号、完成后推进、证据缺失 / 语法错误提示。
"""
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.__main__ import main
from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.interceptors import (
    extract_written_paths,
    is_language_test_command,
    touches_gate_dir,
)
from anti_shortcut.state import StateManager
from conftest import GOOD_TESTS, SPEC


def make_skill(tmp_path: Path, **kwargs) -> AntiShortcutSkill:
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(tmp_path, user_request="实现 fib(n)", **kwargs)


# ---------- 拦截器边界：命令注入变体 ----------

def test_is_language_test_command_substitution_variants():
    """命令替换 / 反引号 / 管道组合仍应被识别为测试命令（宁可多拦）。"""
    cfg = GateConfig()
    assert is_language_test_command("pytest $(rm -rf /)", cfg)
    assert is_language_test_command("npm test `echo hi`", cfg)
    assert is_language_test_command("python -m pytest tests/ | tee log.txt", cfg)
    assert is_language_test_command("go test ./... ; echo done", cfg)
    assert not is_language_test_command("echo pytest", cfg)


def test_extract_written_paths_redirect_variants():
    """1>/2> 重定向、&>> / &> / >| 与引号目标都应被提取。"""
    assert extract_written_paths("echo x 1> fib.py") == ["fib.py"]
    assert extract_written_paths("cmd 2> err.log") == ["err.log"]
    assert extract_written_paths("cmd >> test_x.py 2>&1") == ["test_x.py"]
    assert extract_written_paths("cmd &>> out.ts") == ["out.ts"]
    assert extract_written_paths("cmd &> both.log") == ["both.log"]
    assert extract_written_paths("cmd >| forced.ts") == ["forced.ts"]
    assert extract_written_paths('printf "x" 1> "my file.py"') == ["my file.py"]
    assert extract_written_paths("echo x > file1 > file2") == ["file1", "file2"]


# ---------- 门禁目录全写路径防护 ----------

def test_touches_gate_dir_all_write_methods():
    """.agent_gate 下的所有常见写文件方式都应被标记。"""
    gate = Path("ws/.agent_gate")
    assert touches_gate_dir("tee .agent_gate/state.json", gate)
    assert touches_gate_dir("dd of=.agent_gate/state.json", gate)
    assert touches_gate_dir("sed -i s/a/b/ .agent_gate/state.json", gate)
    assert touches_gate_dir("mv x .agent_gate/state.json", gate)
    assert touches_gate_dir("cp x .agent_gate/state.json", gate)
    assert touches_gate_dir("touch .agent_gate/state.json", gate)
    assert touches_gate_dir("install -m 600 x .agent_gate/state.json", gate)
    assert touches_gate_dir("rm .agent_gate/state.json", gate)
    assert touches_gate_dir("echo x > .agent_gate/state.json", gate)
    assert touches_gate_dir('''python -c 'open(".agent_gate/state.json","w")' ''', gate)
    assert touches_gate_dir('''node -e 'require("fs").writeFileSync(".agent_gate/state.json","{}")' ''', gate)
    assert not touches_gate_dir("cat README.md", gate)


def test_touches_gate_dir_similar_names_not_flagged():
    """相似但不同的目录名不应被误伤，精确目录名仍应被标记。"""
    gate = Path("ws/.agent_gate")
    assert not touches_gate_dir("cat .agent_gate_notes.txt", gate)
    assert not touches_gate_dir("ls .agent_gate.bak", gate)
    assert touches_gate_dir("cat .agent_gate", gate)


# ---------- Skill 权限检查：路径特殊字符 / 穿越 ----------

def test_write_permission_special_char_paths(tmp_path):
    """含空格 / Unicode / 括号的 .py 路径仍应分类为源代码并在阶段 1 被拦截。"""
    skill = make_skill(tmp_path)
    for path in ("my file.py", "src/测试模块.py", "deep/path (copy).py"):
        with pytest.raises(PermissionError, match="实现代码"):
            skill.check_write_permission(path)


def test_write_permission_gate_dir_traversal(tmp_path):
    """相对 / 绝对 / .. 穿越的门禁目录路径都不可写。"""
    skill = make_skill(tmp_path)
    gate = tmp_path / ".agent_gate"
    for bad in (
        ".agent_gate/state.json",
        "./.agent_gate/state.json",
        str(gate / "state.json"),
        "x/../.agent_gate/state.json",
    ):
        with pytest.raises(PermissionError, match="agent_gate"):
            skill.check_write_permission(bad)


def test_exec_gate_dir_all_write_methods_blocked(tmp_path, fake_tools):
    """通过 shell 写入门禁目录的全部常见方式都应被 execute_command 拦截。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    commands = (
        "tee .agent_gate/state.json",
        "dd of=.agent_gate/state.json",
        "sed -i s/a/b/ .agent_gate/state.json",
        "mv x .agent_gate/state.json",
        "cp x .agent_gate/state.json",
        "touch .agent_gate/state.json",
        "echo x > .agent_gate/state.json",
        "install -m 600 x .agent_gate/state.json",
        "rm -f .agent_gate/state.json",
    )
    for cmd in commands:
        with pytest.raises(PermissionError, match="agent_gate"):
            tools["execute_command"](cmd)


def test_exec_test_command_with_substitution_blocked(tmp_path, fake_tools):
    """注入变体的测试命令在阶段 1 仍应被拦截（未完成实现）。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="测试"):
        tools["execute_command"]("pytest $(echo hi)")


def test_exec_write_path_with_spaces_blocked(tmp_path, fake_tools):
    """含空格的源代码写路径通过 shell 也应被阶段门禁拦截。"""
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]('echo x > "my file.py"')


# ---------- CLI 错误处理边界补强 ----------

def test_inspect_corrupted_state_json_mode(capsys, tmp_path):
    """状态文件损坏 + --json：退出码 1，错误输出到 stderr 且无堆栈。"""
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / "state.json").write_text("{broken", encoding="utf-8")
    rc = main(["inspect", "--workspace", str(tmp_path), "--json"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "Traceback" not in err


def test_inspect_state_missing_fields_graceful(capsys, tmp_path):
    """状态文件合法 JSON 但缺字段（版本不符）：友好报错。"""
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / "state.json").write_text("{}", encoding="utf-8")
    rc = main(["inspect", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "版本" in err and "Traceback" not in err


def test_advance_out_of_range_stage_rejected(capsys, tmp_path):
    """越界阶段号（99 / 0 / 负数）都按跳步拒绝，退出码 1。"""
    for to in (99, 0, -1):
        rc = main(["advance", "--workspace", str(tmp_path), "--to", str(to)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "跳跃阶段" in out


def test_advance_complete_rejected(capsys, tmp_path):
    """任务已完成（阶段 6）后再推进应被拒绝。"""
    cfg = load_config(None)
    gate = tmp_path / cfg.gate_dir_name
    gate.mkdir(parents=True, exist_ok=True)
    StateManager(gate / cfg.state_file_name, initial_stage=6)
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "7"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "已完成" in out


def test_advance_tests_missing_rejected(capsys, tmp_path):
    """有 spec 但无测试文件：进入阶段 3 被拒绝并给出原因。"""
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    assert main(["advance", "--workspace", str(tmp_path), "--to", "2"]) == 0
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "3"])
    out = capsys.readouterr().out
    assert rc == 1 and "REJECTED" in out


def test_advance_impl_syntax_error_rejected(capsys, tmp_path):
    """实现语法错误：进入阶段 4 被拒绝（语法检查失败）。"""
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    (tmp_path / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    (tmp_path / "fib.py").write_text("def fib(:\n    pass\n", encoding="utf-8")
    assert main(["advance", "--workspace", str(tmp_path), "--to", "2"]) == 0
    assert main(["advance", "--workspace", str(tmp_path), "--to", "3"]) == 0
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "4"])
    out = capsys.readouterr().out
    assert rc == 1 and "REJECTED" in out


def test_advance_impl_missing_rejected(capsys, tmp_path):
    """有测试无实现：进入阶段 4 被拒绝（缺少源代码）。"""
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    (tmp_path / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    assert main(["advance", "--workspace", str(tmp_path), "--to", "2"]) == 0
    assert main(["advance", "--workspace", str(tmp_path), "--to", "3"]) == 0
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "4"])
    out = capsys.readouterr().out
    assert rc == 1 and "REJECTED" in out
