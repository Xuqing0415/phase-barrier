"""benchmarks/swebench/run_status.py 的契约测试（僵死 running 状态）。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_status = _load(ROOT / "benchmarks" / "swebench" / "run_status.py")


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_running_with_live_pid_is_unchanged():
    data = {"status": "running", "runner_pid": 42}
    assert run_status.normalize_status(data, pid_probe=lambda pid: True) == data


def test_running_with_dead_pid_becomes_stale():
    out = run_status.normalize_status(
        {"status": "running", "runner_pid": 42}, pid_probe=lambda pid: False
    )
    assert out["status"] == "stale"
    assert "42" in out["stale_reason"]


def test_running_without_pid_becomes_stale():
    out = run_status.normalize_status({"status": "running"}, pid_probe=lambda pid: False)
    assert out["status"] == "stale"
    assert "not recorded" in out["stale_reason"]


def test_state_and_pid_aliases_are_supported():
    out = run_status.normalize_status(
        {"state": "running", "pid": 7}, pid_probe=lambda pid: False
    )
    assert out["state"] == "stale"
    assert "7" in out["stale_reason"]


def test_finished_status_is_never_touched():
    data = {"status": "finished", "runner_pid": 42}
    assert run_status.normalize_status(data, pid_probe=lambda pid: False) == data


def test_load_status_does_not_write_back(tmp_path):
    path = tmp_path / "status.json"
    _write(path, {"status": "running", "runner_pid": 42})
    before = path.read_bytes()
    out = run_status.load_status(path, pid_probe=lambda pid: False)
    assert out["status"] == "stale"
    assert path.read_bytes() == before


def test_refresh_status_file_persists_stale_reason(tmp_path):
    path = tmp_path / "status.json"
    _write(path, {"status": "running", "runner_pid": 42})
    out = run_status.refresh_status_file(path, pid_probe=lambda pid: False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == out
    assert saved["status"] == "stale"
    assert "42" in saved["stale_reason"]


def test_refresh_status_file_leaves_live_status_untouched(tmp_path):
    path = tmp_path / "status.json"
    _write(path, {"status": "running", "runner_pid": 42})
    before = path.read_bytes()
    run_status.refresh_status_file(path, pid_probe=lambda pid: True)
    assert path.read_bytes() == before


def test_pid_is_alive_for_current_process():
    assert run_status.pid_is_alive(os.getpid()) is True


def test_pid_is_alive_rejects_invalid_values():
    for value in (None, 0, -1, True, "42"):
        assert run_status.pid_is_alive(value) is False


def test_cli_returns_one_for_stale(tmp_path, capsys):
    path = tmp_path / "status.json"
    _write(path, {"status": "running", "runner_pid": 99999999})
    assert run_status.main([str(path)]) == 1
    assert '"stale"' in capsys.readouterr().out


def test_non_object_json_is_rejected(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("[]", encoding="utf-8")
    try:
        run_status.load_status(path)
    except ValueError as exc:
        assert "顶层必须是对象" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝非对象 JSON")