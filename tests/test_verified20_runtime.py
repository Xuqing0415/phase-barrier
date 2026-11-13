"""A stop must survive Docker's UTF-8 JSON and partial cleanup failures."""
import importlib.util
import json
import subprocess
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "verified_runtime", Path(__file__).resolve().parents[1] / "scripts/verified20_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def container(source, name="/another_verified20_formal"):
    return {"Name": name, "Mounts": [{"Source": str(source)}]}


def fake_docker(info, removed, *, failed_inspect=()):
    def run(cmd, **kwargs):
        assert not kwargs.get("text") and "encoding" not in kwargs
        if cmd[:3] == ["docker", "ps", "-q"]:
            out = "\n".join(info).encode("ascii")
        elif cmd[:2] == ["docker", "inspect"]:
            if cmd[2] in failed_inspect:
                return subprocess.CompletedProcess(cmd, 1, b"", b"inspect failed")
            out = json.dumps([info[cmd[2]]], ensure_ascii=False).encode("utf-8")
        elif cmd[:3] == ["docker", "rm", "-f"]:
            removed.append(cmd[3])
            out = b""
        else:
            raise AssertionError(cmd)
        return subprocess.CompletedProcess(cmd, 0, out, b"")
    return run


def test_unicode_inspect_cleans_only_this_batch_and_preserves_evidence(tmp_path):
    root = tmp_path / "正式批"
    root.mkdir()
    patch = root / "model_patch.diff"
    patch.write_bytes(b"saved patch")
    removed = []
    info = {"aaa": container(root / "agent_runs"),
            "bbb": container(tmp_path / "unrelated", name="/also_verified20_formal")}
    result = runtime.stop_verified_batch(root, 1234, "model_call_error", pid_probe=lambda _: False,
                                         run=fake_docker(info, removed))
    assert removed == ["aaa"] and result["cleanup_complete"]
    assert patch.read_bytes() == b"saved patch"
    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "halted" and status["halt_reason"] == "model_call_error"


def test_one_failed_inspect_does_not_prevent_remaining_cleanup(tmp_path):
    removed = []
    info = {"aaa": container(tmp_path), "bbb": container(tmp_path)}
    result = runtime.stop_verified_batch(tmp_path, 1234, "model_call_error", pid_probe=lambda _: False,
                                         run=fake_docker(info, removed, failed_inspect=("aaa",)))
    assert removed == ["bbb"] and not result["cleanup_complete"]
    assert result["cleanup_errors"][0]["container"] == "aaa"
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "halted"


def test_docker_unavailable_still_records_halt(tmp_path):
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", 30)
    result = runtime.stop_verified_batch(tmp_path, 1234, "grade_infra_failure", pid_probe=lambda _: False,
                                         run=unavailable)
    assert not result["cleanup_complete"]
    assert result["cleanup_errors"] == [{"operation": "list_containers", "error": "TimeoutExpired"}]
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "halted"


def test_process_stop_failure_is_visible_and_docker_cleanup_still_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    removed = []
    docker = fake_docker({"aaa": container(tmp_path)}, removed)
    def run(cmd, **kwargs):
        if cmd[0] == "taskkill":
            raise PermissionError("not permitted")
        return docker(cmd, **kwargs)
    result = runtime.stop_verified_batch(tmp_path, 1234, "model_call_error", pid_probe=lambda _: True, run=run)
    assert removed == ["aaa"] and result["runner_alive_after_stop"]
    assert not result["cleanup_complete"]
    assert result["cleanup_errors"][0]["operation"] == "stop_runner"


def test_docker_desktop_path_spelling_and_path_boundary():
    assert runtime._host_path(r"D:\实验\formal\agent_runs") == runtime._host_path(
        "/run/desktop/mnt/host/d/实验/formal/agent_runs")
    root = Path("formal").resolve()
    assert runtime.owns_container(container(root / "agent_runs"), root)
    assert not runtime.owns_container(container(str(root) + "_unrelated"), root)
