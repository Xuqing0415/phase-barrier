"""v0.18.0 CLI 门禁测试：write / exec 子命令。"""
import io
import json
import sys

import pytest

from anti_shortcut.__main__ import main
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC


def _advance(ws, to):
    return main(["advance", "--workspace", str(ws), "--to", str(to)])


def _write(ws, path, content=None, *, stdin=None, json_out=False, extra=None):
    argv = ["write", "--workspace", str(ws), "--path", path]
    if json_out:
        argv.append("--json")
    if extra:
        argv.extend(extra)
    if stdin is not None:
        argv.append("--stdin")
    else:
        argv.extend(["--content", content])
    return main(argv)


# ---------- write ----------


def test_cli_write_source_denied_exit2(capsys, tmp_path):
    rc = _write(tmp_path, "fib.py", "x")
    out = capsys.readouterr().out
    assert rc == 2 and "DENIED" in out and "实现代码" in out


def test_cli_write_test_allowed_at_spec_stage(capsys, tmp_path):
    rc = _write(tmp_path, "test_fib.py", GOOD_TESTS)
    out = capsys.readouterr().out
    assert rc == 0 and "OK" in out
    assert (tmp_path / "test_fib.py").read_text(encoding="utf-8") == GOOD_TESTS


def test_cli_write_spec_ok_json(capsys, tmp_path):
    rc = _write(tmp_path, "spec.md", SPEC, json_out=True)
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] and payload["kind"] == "other"


def test_cli_write_from_stdin(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin content"))
    rc = _write(tmp_path, "notes.txt", stdin=True)
    capsys.readouterr()
    assert rc == 0
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "stdin content"


def test_cli_write_content_and_stdin_conflict(capsys, tmp_path):
    rc = main(
        [
            "write",
            "--workspace",
            str(tmp_path),
            "--path",
            "a.txt",
            "--content",
            "x",
            "--stdin",
        ]
    )
    out = capsys.readouterr().err
    assert rc == 1 and "不能同时使用" in out


def test_cli_write_missing_content(capsys, tmp_path):
    rc = main(["write", "--workspace", str(tmp_path), "--path", "a.txt"])
    out = capsys.readouterr().err
    assert rc == 1 and "必须提供" in out


def test_cli_write_path_escape_rejected(capsys, tmp_path):
    rc = _write(tmp_path, "../evil.txt", "x")
    out = capsys.readouterr().err
    assert rc == 1 and "越出工作区" in out


def test_cli_write_gate_dir_rejected(capsys, tmp_path):
    rc = _write(tmp_path, ".agent_gate/state.json", "{}")
    out = capsys.readouterr().out
    assert rc == 2 and "DENIED" in out


def test_cli_write_missing_workspace(capsys, tmp_path):
    rc = main(["write", "--workspace", str(tmp_path / "nope"), "--path", "a.txt", "--content", "x"])
    out = capsys.readouterr().err
    assert rc == 1 and "不存在" in out


# ---------- exec ----------


def test_cli_exec_test_command_denied_exit2(capsys, tmp_path):
    rc = main(["exec", "--workspace", str(tmp_path), "--command", "python -m pytest -q"])
    out = capsys.readouterr().out
    assert rc == 2 and "DENIED" in out and "实现代码" in out


def test_cli_exec_allowed_returns_output(capsys, tmp_path):
    rc = main(["exec", "--workspace", str(tmp_path), "--command", 'python -c "print(42)"'])
    out = capsys.readouterr().out
    assert rc == 0
    assert "exit_code=0" in out and "42" in out


def test_cli_exec_command_failure_propagates_exit(capsys, tmp_path):
    rc = main(
        ["exec", "--workspace", str(tmp_path), "--command", 'python -c "import sys; sys.exit(3)"']
    )
    out = capsys.readouterr().out
    assert rc == 3 and "exit_code=3" in out


def test_cli_exec_json_output(capsys, tmp_path):
    rc = main(
        [
            "exec",
            "--workspace",
            str(tmp_path),
            "--command",
            'python -c "print(7)"',
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] and payload["exit_code"] == 0
    assert payload["recorded_test_run"] is False


def test_cli_exec_invalid_timeout_rejected(capsys, tmp_path):
    rc = main(["exec", "--workspace", str(tmp_path), "--command", "echo hi", "--timeout", "0"])
    out = capsys.readouterr().err
    assert rc == 1 and "timeout" in out


def test_cli_exec_timeout_returns_nonzero(capsys, tmp_path):
    rc = main(
        [
            "exec",
            "--workspace",
            str(tmp_path),
            "--command",
            'python -c "import time; time.sleep(30)"',
            "--timeout",
            "1",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1 and "超时" in out


def test_cli_exec_records_test_run(capsys, tmp_path):
    """完整 CLI 流程：write spec -> advance 2 -> write tests -> advance 3 ->
    write impl -> advance 4 -> exec pytest（自动记录测试结果）。"""
    assert _write(tmp_path, "spec.md", SPEC) == 0
    capsys.readouterr()
    assert _advance(tmp_path, 2) == 0
    capsys.readouterr()
    assert _write(tmp_path, "test_fib.py", GOOD_TESTS) == 0
    capsys.readouterr()
    assert _advance(tmp_path, 3) == 0
    capsys.readouterr()
    assert _write(tmp_path, "fib.py", GOOD_IMPL) == 0
    capsys.readouterr()
    assert _advance(tmp_path, 4) == 0
    capsys.readouterr()

    rc = main(
        ["exec", "--workspace", str(tmp_path), "--command", "python -m pytest test_fib.py -q", "--json"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["recorded_test_run"] is True

    from anti_shortcut.skill import AntiShortcutSkill

    skill = AntiShortcutSkill(tmp_path)
    try:
        record = skill.state.get_evidence("last_test_run")
        assert record is not None and record.get("passed") is True
    finally:
        skill.close()