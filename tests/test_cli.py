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
