# -*- coding: utf-8 -*-
"""CLI 补测（v0.54.1）：init / init-requirement / check / query / review-approve
/ export-evidence / rotate-key / sidecar 转发 / 错误码映射。

`test_cli.py`、`test_cli_gate_commands.py` 覆盖主干；本文件补齐异常分支与
较少走到的输出模式，让 CLI 不再有整段未执行代码。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import anti_shortcut.__main__ as cli
import anti_shortcut.sidecar as sidecar_mod
from anti_shortcut.__main__ import main
from anti_shortcut.config import GateConfig
from anti_shortcut.defense._common import evidence_path
from conftest import SPEC


def _last_test_run_ws(ws: Path) -> None:
    from anti_shortcut.skill import AntiShortcutSkill

    skill = AntiShortcutSkill(ws)
    try:
        skill.state.set_evidence(
            "last_test_run", {"exit_code": 0, "passed": True, "summary": "3 passed"}
        )
    finally:
        skill.close()


# ---------- init ----------


def test_cli_init_text_and_json(capsys, tmp_path):
    rc = main(["init", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "OK" in out
    assert (tmp_path / "config.yaml").is_file()

    rc2 = main(
        [
            "init",
            "--workspace",
            str(tmp_path),
            "--force",
            "--json",
            "--language",
            "python",
            "--rules",
            "no_path_traversal, no_shell_injection",
            "--with-coverage",
            "--coverage-threshold",
            "75",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and payload["language"] == "python"
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "no_path_traversal" in text and "no_shell_injection" in text


def test_cli_init_missing_workspace(capsys, tmp_path):
    rc = main(["init", "--workspace", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert rc == 1 and "不存在" in err


# ---------- init-requirement ----------


def test_cli_init_requirement_example_json_and_overwrite(capsys, tmp_path):
    rc = main(["init-requirement", "--workspace", str(tmp_path), "--example", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["ok"] is True

    rc2 = main(["init-requirement", "--workspace", str(tmp_path), "--example"])
    assert rc2 == 1 and "已存在" in capsys.readouterr().err

    rc3 = main(["init-requirement", "--workspace", str(tmp_path), "--example", "--force"])
    assert rc3 == 0
    capsys.readouterr()


def test_cli_init_requirement_invalid_then_forced(capsys, tmp_path):
    rc = main(["init-requirement", "--workspace", str(tmp_path), "--goal", "只有目标"])
    err = capsys.readouterr().err
    assert rc == 1 and "校验未通过" in err

    rc2 = main(
        ["init-requirement", "--workspace", str(tmp_path), "--goal", "只有目标", "--force", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and payload["valid"] is False

    rc3 = main(
        [
            "init-requirement",
            "--workspace",
            str(tmp_path),
            "--goal",
            "完整需求",
            "--forbidden",
            "禁止明文密码",
            "--interfaces",
            "def f()->int;def g()->int",
            "--acceptance",
            "f()返回1;g()返回2",
        ]
    )
    assert rc3 == 1 and "已存在" in capsys.readouterr().err


def test_cli_init_requirement_missing_workspace(capsys, tmp_path):
    rc = main(["init-requirement", "--workspace", str(tmp_path / "nope"), "--example"])
    assert rc == 1 and "不存在" in capsys.readouterr().err


# ---------- review-approve ----------


def test_cli_review_approve_without_request_file(capsys, tmp_path):
    rc = main(["review-approve", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1 and "human_review_request.json" in err


def test_cli_review_approve_reads_latest_request_json(capsys, tmp_path):
    cfg = GateConfig()
    evidence_path(tmp_path, cfg, "human_review_request.json").write_text(
        json.dumps({"request_id": "req-latest"}), encoding="utf-8"
    )
    rc = main(["review-approve", "--workspace", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["request_id"] == "req-latest"


def test_cli_review_approve_missing_workspace(capsys, tmp_path):
    rc = main(["review-approve", "--workspace", str(tmp_path / "nope"), "--request-id", "x"])
    assert rc == 1 and "不存在" in capsys.readouterr().err


# ---------- inspect / check ----------


def test_cli_inspect_prints_last_test_run(capsys, tmp_path):
    _last_test_run_ws(tmp_path)
    rc = main(["inspect", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "last_test_run" in out and "3 passed" in out


def test_cli_check_denied_then_allowed(capsys, tmp_path):
    rc = main(["check", "--workspace", str(tmp_path), "--stage", "2"])
    err = capsys.readouterr().err
    assert rc == 1 and "DENIED" in err

    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    assert main(["advance", "--workspace", str(tmp_path), "--to", "2"]) == 0
    capsys.readouterr()

    rc2 = main(["check", "--workspace", str(tmp_path), "--stage", "2"])
    assert rc2 == 0 and "OK" in capsys.readouterr().out

    rc3 = main(["check", "--workspace", str(tmp_path), "--stage", "2", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc3 == 0 and payload["allowed"] is True


def test_cli_check_missing_workspace(capsys, tmp_path):
    rc = main(["check", "--workspace", str(tmp_path / "nope"), "--stage", "2"])
    assert rc == 1 and "不存在" in capsys.readouterr().err


# ---------- query ----------


def test_cli_query_required_evidence_text_and_json(capsys, tmp_path):
    rc = main(["query", "--workspace", str(tmp_path), "--required-evidence", "0"])
    out = capsys.readouterr().out
    assert rc == 0 and "阶段" in out

    rc2 = main(["query", "--workspace", str(tmp_path), "--required-evidence", "1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and isinstance(payload, list)


def test_cli_query_last_test_run_both_shapes(capsys, tmp_path):
    rc = main(["query", "--workspace", str(tmp_path), "--last-test-run"])
    assert rc == 0 and "None" in capsys.readouterr().out

    _last_test_run_ws(tmp_path)
    rc2 = main(["query", "--workspace", str(tmp_path), "--last-test-run"])
    out = capsys.readouterr().out
    assert rc2 == 0 and "passed=True" in out

    rc3 = main(["query", "--workspace", str(tmp_path), "--last-test-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc3 == 0 and payload["passed"] is True


def test_cli_query_stage_history_and_git_state(capsys, tmp_path):
    rc = main(["query", "--workspace", str(tmp_path), "--stage-history"])
    out = capsys.readouterr().out
    assert rc == 0 and "阶段 0" in out

    rc2 = main(["query", "--workspace", str(tmp_path), "--stage-history", "--json"])
    assert rc2 == 0 and json.loads(capsys.readouterr().out)

    rc3 = main(["query", "--workspace", str(tmp_path), "--has-uncommitted-changes"])
    out3 = capsys.readouterr().out
    assert rc3 == 0 and "has_uncommitted_changes" in out3


def test_cli_query_missing_workspace(capsys, tmp_path):
    rc = main(["query", "--workspace", str(tmp_path / "nope"), "--last-test-run"])
    assert rc == 1 and "不存在" in capsys.readouterr().err


# ---------- export-evidence / rotate-key ----------


class _FakeManifest:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def verify(self, workspace):
        return True, []

    def entries(self):
        return {"missing.txt": {"stage": 1}, "boom.txt": {"stage": 1}}

    def is_signed(self) -> bool:
        return False


def test_cli_export_evidence_records_missing_and_unreadable(monkeypatch, capsys, tmp_path):
    (tmp_path / "boom.txt").write_text("data", encoding="utf-8")
    real_sha = cli.sha256_file

    def fake_sha(path):
        if Path(path).name == "boom.txt":
            raise OSError("locked by another process")
        return real_sha(path)

    monkeypatch.setattr(cli, "EvidenceManifest", _FakeManifest)
    monkeypatch.setattr(cli, "sha256_file", fake_sha)
    rc = main(["export-evidence", "--workspace", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["files"]["boom.txt"]["error"] == "不可读"
    assert "missing.txt" not in payload["files"]


def test_cli_rotate_key_missing_workspace(capsys, tmp_path):
    rc = main(["rotate-key", "--workspace", str(tmp_path / "nope"), "--to", "new-key"])
    assert rc == 1 and "不存在" in capsys.readouterr().err


# ---------- write / exec 拒绝路径（JSON 输出） ----------


def test_cli_write_denied_json(capsys, tmp_path):
    rc = main(
        [
            "write",
            "--workspace",
            str(tmp_path),
            "--path",
            ".agent_gate/state.json",
            "--content",
            "{}",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2 and payload["ok"] is False


def test_cli_exec_denied_json_and_missing_workspace(capsys, tmp_path):
    rc = main(["exec", "--workspace", str(tmp_path), "--command", "python -m pytest -q", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2 and payload["ok"] is False

    rc2 = main(["exec", "--workspace", str(tmp_path / "nope"), "--command", "echo hi"])
    assert rc2 == 1 and "不存在" in capsys.readouterr().err


# ---------- plugin-verify / sidecar / 顶层错误映射 ----------


def test_cli_plugin_verify_reports_no_third_party(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "verify_plugins", lambda: [])
    monkeypatch.setattr(cli, "discover_plugins", lambda: [])
    monkeypatch.setattr(cli, "summarize_plugin_verification", lambda results: (True, "all good"))
    rc = main(["plugin-verify", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "未安装任何第三方插件" in out


def test_cli_sidecar_forwards_every_flag(monkeypatch, capsys, tmp_path):
    captured: dict = {}

    def fake_sidecar_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(sidecar_mod, "main", fake_sidecar_main)
    rc = main(
        [
            "sidecar",
            "--workspace",
            str(tmp_path),
            "--config",
            "cfg.yaml",
            "--user-request",
            "实现登录",
            "--state-key",
            "sk-1",
            "--tls-cert",
            "cert.pem",
            "--tls-key",
            "key.pem",
            "--tls-client-ca",
            "ca.pem",
            "--host",
            "127.0.0.1",
            "--port",
            "9099",
        ]
    )
    assert rc == 0
    argv = captured["argv"]
    for flag, value in (
        ("--workspace", str(tmp_path)),
        ("--config", "cfg.yaml"),
        ("--user-request", "实现登录"),
        ("--state-key", "sk-1"),
        ("--tls-cert", "cert.pem"),
        ("--tls-key", "key.pem"),
        ("--tls-client-ca", "ca.pem"),
        ("--host", "127.0.0.1"),
        ("--port", "9099"),
    ):
        assert flag in argv
        assert argv[argv.index(flag) + 1] == value


def test_cli_main_maps_oserror_to_exit_1(monkeypatch, capsys, tmp_path):
    def boom(args):
        raise OSError("disk on fire")

    monkeypatch.setattr(cli, "_cmd_init", boom)
    rc = main(["init", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1 and "无法访问工作区" in err

def test_cli_sidecar_defaults_omit_optional_flags(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_sidecar_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(sidecar_mod, "main", fake_sidecar_main)
    rc = main(["sidecar", "--workspace", str(tmp_path)])
    assert rc == 0
    argv = captured["argv"]
    assert "--config" not in argv and "--user-request" not in argv
    assert "--state-key" not in argv and "--tls-cert" not in argv
    assert "--tls-key" not in argv and "--tls-client-ca" not in argv
