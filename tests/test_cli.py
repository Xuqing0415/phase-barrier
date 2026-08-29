"""CLI 测试：inspect / advance / 退出码 / JSON 输出。"""
from pathlib import Path

import pytest

from anti_shortcut.__main__ import main
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


def run_cli(argv, workspace):
    return main(argv + ["--workspace", str(workspace)])


def test_inspect_text(capsys, tmp_path):
    rc = main(["inspect", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "current_stage   : 1" in out


def test_inspect_json(capsys, tmp_path):
    rc = main(["inspect", "--workspace", str(tmp_path), "--json"])
    import json

    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["current_stage"] == 1
    assert payload["complete"] is False
    assert payload["completed_stages"] == [0]


def test_advance_rejected_without_evidence(capsys, tmp_path):
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "2"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "REJECTED" in out and "spec" in out


def test_advance_ok_with_spec(capsys, tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out and "\u9636\u6bb5 2" in out


def test_advance_jump_rejected(capsys, tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    rc = main(["advance", "--workspace", str(tmp_path), "--to", "3"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "\u8df3\u8dc3\u9636\u6bb5" in out


def test_advance_json(capsys, tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    import json

    rc = main(["advance", "--workspace", str(tmp_path), "--to", "2", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["stage"] == 2

def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "anti_shortcut" in capsys.readouterr().out


def test_unknown_command_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        main(["frobnicate"])
    assert exc_info.value.code == 2


def test_advance_requires_to():
    with pytest.raises(SystemExit) as exc_info:
        main(["advance", "--workspace", "."])
    assert exc_info.value.code == 2


def test_advance_invalid_to_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        main(["advance", "--to", "abc", "--workspace", "."])
    assert exc_info.value.code == 2


def test_inspect_missing_config_graceful(capsys, tmp_path):
    missing = tmp_path / "nope.yaml"
    rc = main(["inspect", "--workspace", str(tmp_path), "--config", str(missing)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "nope.yaml" in err


def test_inspect_invalid_config_graceful(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("min_test_functions: [oops\n", encoding="utf-8")
    rc = main(["inspect", "--workspace", str(tmp_path), "--config", str(bad)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err


# ---------- v0.3.1 新增：CLI 错误处理边界 ----------

def test_inspect_corrupted_state_graceful(capsys, tmp_path):
    """状态文件损坏（非法 JSON）时应友好报错，不输出堆栈。"""
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / "state.json").write_text("{not json", encoding="utf-8")
    rc = main(["inspect", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "状态" in err
    assert "Traceback" not in err


def test_inspect_incompatible_state_version_graceful(capsys, tmp_path):
    """状态文件版本不兼容时应友好报错。"""
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / "state.json").write_text('{"version": 999}', encoding="utf-8")
    rc = main(["inspect", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "版本" in err
    assert "Traceback" not in err


def test_inspect_missing_workspace_graceful(capsys, tmp_path):
    """工作区不存在时应报错，且不静默创建目录树。"""
    missing = tmp_path / "no_such_ws"
    rc = main(["inspect", "--workspace", str(missing)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "工作区" in err
    assert not missing.exists()
