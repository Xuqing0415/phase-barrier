"""状态机模块测试。"""
from pathlib import Path

import pytest

from anti_shortcut.state import StateManager


def make_state(tmp_path: Path, user_request="\u6d4b\u8bd5\u9700\u6c42") -> StateManager:
    return StateManager(tmp_path / "state.json", user_request=user_request)


def test_bootstrap(tmp_path):
    s = make_state(tmp_path)
    assert s.current_stage == 1
    assert s.completed_stages == [0]
    assert s.get_evidence("user_request") == "\u6d4b\u8bd5\u9700\u6c42"
    assert (tmp_path / "state.json").exists()


def test_advance_persists(tmp_path):
    s = make_state(tmp_path)
    s.advance(2, {"spec": "ok"})
    reloaded = StateManager(tmp_path / "state.json")
    assert reloaded.current_stage == 2
    assert reloaded.completed_stages == [0, 1]
    assert reloaded.get_evidence("user_request") == "\u6d4b\u8bd5\u9700\u6c42"


def test_jump_rejected(tmp_path):
    s = make_state(tmp_path)
    with pytest.raises(ValueError):
        s.advance(3)


def test_stage4_branch_allowed(tmp_path):
    s = make_state(tmp_path)
    s.advance(2)
    s.advance(3)
    s.advance(4)
    # \u4ece\u9636\u6bb5 4 \u53ef\u4ee5\u8fdb\u5165 5 \u6216 6\uff08\u7ed5\u8fc7\u4fee\u590d\uff09
    s.advance(6)
    assert s.current_stage == 6


def test_mark_test_run(tmp_path):
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "summary": "3 passed"})
    tr = s.get_evidence("last_test_run")
    assert tr["exit_code"] == 0
    assert tr["passed"] is True
    assert "at_epoch" in tr and "at" in tr


def test_mark_source_change(tmp_path):
    s = make_state(tmp_path)
    assert s.get_evidence("last_source_change_at_epoch") is None
    s.mark_source_change("fib.py")
    assert s.get_evidence("last_source_change_at_epoch") is not None


def test_is_complete(tmp_path):
    s = make_state(tmp_path)
    assert not s.is_complete
    s.advance(2)
    s.advance(3)
    s.advance(4)
    s.advance(5)
    s.advance(6)
    assert s.is_complete
