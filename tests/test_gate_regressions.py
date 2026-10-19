"""命令副作用、失败测试、并发阶段提交和代理内容规则的回归覆盖。"""
import os
import subprocess
from unittest.mock import Mock

import pytest

from anti_shortcut.proxy import GateProxy, ProxyError, WriteDenied
from anti_shortcut.skill import AntiShortcutSkill
from anti_shortcut.state import StateManager


def make_skill(tmp_path, stage=6, config=None):
    skill = AntiShortcutSkill(tmp_path, config=config, user_request="regression")
    for number in range(2, stage + 1):
        skill.state.advance(number)
    skill.state.mark_test_run({"passed": True, "exit_code": 0})
    return skill


@pytest.mark.parametrize("entry", ["proxy", "wrapper"])
def test_shell_source_change_invalidates_delivery(tmp_path, entry):
    skill = make_skill(tmp_path)
    (tmp_path / "impl.py").write_text("value = 1\n", encoding="utf-8")
    assert skill.state.delivery_clean()
    if entry == "proxy":
        result = GateProxy(skill).execute_command("echo value = 2 > impl.py")
    else:
        def execute(command):
            proc = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True)
            return {"exit_code": proc.returncode, "output": proc.stdout}
        result = skill.wrap_execute_command(execute)("echo value = 2 > impl.py")
    assert result["exit_code"] == 0
    assert not skill.state.delivery_clean()
    assert StateManager(skill.state.state_file).get_evidence("last_source_change_path") == "impl.py"


@pytest.mark.parametrize("change", ["create", "modify", "delete"])
@pytest.mark.parametrize("outcome", ["success", "nonzero", "exception"])
def test_command_changes_recorded_on_all_exit_paths(tmp_path, change, outcome):
    skill = make_skill(tmp_path)
    path = tmp_path / "test_impl.py"
    if change != "create":
        path.write_text("old", encoding="utf-8")
    metadata = path.stat() if path.exists() else None

    def execute(command):
        if change == "delete":
            path.unlink()
        else:
            path.write_text("new", encoding="utf-8")
            if metadata:
                # 大小和时间戳不变，内容变化仍须被发现。
                os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        if outcome == "exception":
            raise RuntimeError("tool failed after writing")
        return {"exit_code": 1 if outcome == "nonzero" else 0, "output": ""}

    guarded = skill.wrap_execute_command(execute)
    if outcome == "exception":
        with pytest.raises(RuntimeError):
            guarded("custom-build")
    else:
        guarded("custom-build")
    assert not skill.state.delivery_clean()


def test_test_that_changes_source_needs_another_run(tmp_path):
    skill = make_skill(tmp_path)

    def execute(command):
        assert not StateManager(skill.state.state_file).delivery_clean()
        (tmp_path / "impl.py").write_text("value = 2", encoding="utf-8")
        return {"exit_code": 0, "output": "1 passed"}

    skill.wrap_execute_command(execute)("python -m pytest")
    assert skill.state.get_evidence("last_test_run")["passed"]
    assert not skill.state.delivery_clean()
    skill.wrap_execute_command(lambda command: {"exit_code": 0, "output": "1 passed"})("pytest")
    assert skill.state.delivery_clean()


def test_read_only_command_preserves_green_result(tmp_path):
    skill = make_skill(tmp_path)
    (tmp_path / "impl.py").write_text("value = 1", encoding="utf-8")
    skill.wrap_execute_command(lambda command: "ok")("custom-read")
    assert skill.state.delivery_clean()


def test_proxy_timeout_replaces_prior_green(tmp_path, monkeypatch):
    skill = make_skill(tmp_path, stage=4)
    proc = Mock()
    proc.communicate.side_effect = subprocess.TimeoutExpired("pytest", 1)
    monkeypatch.setattr("anti_shortcut.proxy.subprocess.Popen", lambda *a, **kw: proc)
    monkeypatch.setattr("anti_shortcut.proxy._terminate_process_tree", lambda proc: None)
    result = GateProxy(skill).execute_command("pytest", timeout=1)
    assert result["timed_out"] and result["recorded_test_run"]
    assert not skill.state.get_evidence("last_test_run")["passed"]
    advanced = skill.advance_stage(5)
    assert advanced["success"] and advanced["stage"] == 5


@pytest.mark.parametrize("entry", ["proxy", "wrapper"])
def test_test_tool_exception_replaces_prior_green(tmp_path, monkeypatch, entry):
    skill = make_skill(tmp_path)

    def fail(*a, **kw):
        raise OSError("cannot start")

    if entry == "proxy":
        monkeypatch.setattr("anti_shortcut.proxy.subprocess.Popen", fail)
        execute = GateProxy(skill).execute_command
        error = ProxyError
    else:
        execute = skill.wrap_execute_command(fail)
        error = OSError
    with pytest.raises(error):
        execute("pytest")
    assert not StateManager(skill.state.state_file).delivery_clean()


@pytest.mark.parametrize("stage", [4, 5])
@pytest.mark.parametrize("update", ["source", "test", "stage"])
def test_concurrent_update_between_validation_and_commit_is_rejected(tmp_path, monkeypatch, stage, update):
    skill = make_skill(tmp_path, stage=stage)
    other = StateManager(skill.state.state_file)

    def defense(*args):
        if update == "source":
            other.mark_source_change("impl.py")
        elif update == "test":
            other.mark_test_run({"passed": False, "exit_code": 1})
        else:
            other.advance(stage + 1)
        return True, "ok", {}

    monkeypatch.setattr("anti_shortcut.skill.run_defense_checks", defense)
    result = skill.advance_stage(stage + 1)
    assert not result["success"]
    assert "状态已变化" in result["error"]
    latest = StateManager(skill.state.state_file)
    assert latest.current_stage == (stage + 1 if update == "stage" else stage)


def test_stage_validation_reloads_another_instances_update(tmp_path):
    skill = make_skill(tmp_path, stage=4)
    other = StateManager(skill.state.state_file)
    other.mark_source_change("impl.py")
    result = skill.advance_stage(5)
    assert result["success"] and result["stage"] == 5


def test_validator_can_persist_its_own_evidence(tmp_path, monkeypatch):
    skill = make_skill(tmp_path, stage=4)

    def defense(*args):
        skill.state.set_evidence("requirement_template", {"validated": True})
        return True, "ok", {}

    monkeypatch.setattr("anti_shortcut.skill.run_defense_checks", defense)
    result = skill.advance_stage(5)
    assert result["success"] and result["stage"] == 6
    assert StateManager(skill.state.state_file).get_evidence("requirement_template") == {"validated": True}


@pytest.mark.parametrize("config, bad, good", [
    ({"rules": ["require_license_header"], "rules_options": {"license_header": "Copyright Example"}},
     "value = 1", "# Copyright Example\nvalue = 1"),
    ({"rules": ["no_hardcoded_secrets"]},
     'password = "hunter2-secret-value"', "value = 1"),
])
def test_proxy_enforces_content_rules(tmp_path, config, bad, good):
    skill = make_skill(tmp_path, stage=3, config=config)
    proxy = GateProxy(skill)
    with pytest.raises(PermissionError):
        skill.check_write_permission(tmp_path / "impl.py", bad)
    with pytest.raises(WriteDenied):
        proxy.write_file("impl.py", bad)
    assert not (tmp_path / "impl.py").exists()
    assert proxy.write_file("impl.py", good)["ok"]
