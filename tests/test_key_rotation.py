"""HMAC 状态签名密钥轮换测试（v0.9.0）。"""
import json

import pytest

from anti_shortcut.__main__ import main
from anti_shortcut.state import StateManager, TamperedStateError
from anti_shortcut.evidence import EvidenceManifest, EvidenceTamperedError, rotate_signing_key
from anti_shortcut.skill import AntiShortcutSkill
from conftest import SPEC, USER_REQUEST


def state_path(tmp_path):
    return tmp_path / ".agent_gate" / "state.json"


def make_state(tmp_path, key="old-key"):
    gate = tmp_path / ".agent_gate"
    gate.mkdir(parents=True, exist_ok=True)
    return StateManager(state_path(tmp_path), user_request="r", hmac_key=key)


def test_rotate_key_resigns(tmp_path):
    s = make_state(tmp_path)
    s.advance(2, {"spec": "ok"})
    s.rotate_key("new-key")
    with pytest.raises(TamperedStateError):
        StateManager(state_path(tmp_path), hmac_key="old-key")
    reloaded = StateManager(state_path(tmp_path), hmac_key="new-key")
    assert reloaded.current_stage == 2


def test_rotate_key_empty_rejected(tmp_path):
    s = make_state(tmp_path)
    with pytest.raises(ValueError):
        s.rotate_key("")


def test_rotate_key_signed_without_keys_rejected(tmp_path):
    make_state(tmp_path)  # 已签名（old-key）
    s = StateManager(state_path(tmp_path))  # 无密钥加载（不校验）
    with pytest.raises(TamperedStateError):
        s.rotate_key("new-key")


def test_rotate_key_enables_signing_on_unsigned(tmp_path):
    """未签名状态 -> 视为“启用签名”迁移，直接用新密钥签名。"""
    StateManager(state_path(tmp_path), user_request="r")  # 无签名
    s = StateManager(state_path(tmp_path))  # 无密钥加载
    s.rotate_key("new-key")
    data = json.loads((state_path(tmp_path)).read_text(encoding="utf-8"))
    assert data["signature"].startswith("v1:")
    assert StateManager(state_path(tmp_path), hmac_key="new-key").current_stage == 1


def test_rotation_keys_transition(tmp_path):
    """部署新密钥 + 旧密钥轮换列表：旧签名可读，写入后自动换用新签名。"""
    make_state(tmp_path, key="old-key")
    s = StateManager(state_path(tmp_path), hmac_key="new-key", hmac_keys=["old-key"])
    assert s.current_stage == 1
    s.advance(2, {"spec": "ok"})
    with pytest.raises(TamperedStateError):
        StateManager(state_path(tmp_path), hmac_key="old-key")
    assert StateManager(state_path(tmp_path), hmac_key="new-key").current_stage == 2


def test_rotation_keys_env(tmp_path, monkeypatch):
    make_state(tmp_path, key="old-key")
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEY", "new-key")
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEYS", "old-key,another-old")
    s = StateManager(state_path(tmp_path))
    assert s.current_stage == 1


def test_rotate_key_keep_old_grace(tmp_path):
    """keep_old=True：同一进程内旧密钥保留为轮换期验证密钥。"""
    s = make_state(tmp_path, key="old-key")
    s.advance(2, {"spec": "ok"})
    s.rotate_key("new-key", keep_old=True)
    # 重新加载（新主密钥 + 旧密钥轮换列表）仍可验证
    reloaded = StateManager(
        state_path(tmp_path), hmac_key="new-key", hmac_keys=["old-key"]
    )
    assert reloaded.current_stage == 2


def test_cli_rotate_key(capsys, tmp_path):
    make_state(tmp_path, key="old-key")
    rc = main(
        [
            "rotate-key",
            "--workspace",
            str(tmp_path),
            "--from",
            "old-key",
            "--to",
            "new-key",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "已轮换" in out
    assert StateManager(state_path(tmp_path), hmac_key="new-key").current_stage == 1
    with pytest.raises(TamperedStateError):
        StateManager(state_path(tmp_path), hmac_key="old-key")


def test_cli_rotate_key_wrong_from_fails(capsys, tmp_path):
    make_state(tmp_path, key="old-key")
    rc = main(
        [
            "rotate-key",
            "--workspace",
            str(tmp_path),
            "--from",
            "wrong",
            "--to",
            "new-key",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "状态" in err


def skill_with_manifest(tmp_path, key="old-key"):
    skill = AntiShortcutSkill(tmp_path, config={"state_hmac_key": key}, user_request=USER_REQUEST)
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    assert skill.advance_stage(2)["success"]
    return skill


@pytest.mark.parametrize("keep_old", [False, True])
@pytest.mark.parametrize("old_key", ["old-key", None])
def test_cli_rotation_keeps_real_evidence_readable(tmp_path, keep_old, old_key):
    skill = skill_with_manifest(tmp_path, old_key)
    entries = skill.evidence_manifest.entries()
    args = ["rotate-key", "--workspace", str(tmp_path), "--to", "new-key"]
    if old_key:
        args += ["--from", old_key]
    if keep_old:
        args += ["--keep-old"]
    assert main(args) == 0
    restarted = AntiShortcutSkill(tmp_path, config={"state_hmac_key": "new-key"})
    assert restarted.current_stage == 2
    assert restarted.evidence_manifest.entries() == entries
    assert restarted.verify_evidence() == (True, [])
    assert "rotation_sigs" not in json.loads(restarted.evidence_manifest.manifest_file.read_text(encoding="utf-8"))


def test_skill_and_evidence_cli_accept_rotation_keys(tmp_path, monkeypatch):
    skill_with_manifest(tmp_path)
    cfg = {"state_hmac_key": "new-key", "state_hmac_keys": ["old-key"]}
    assert AntiShortcutSkill(tmp_path, config=cfg).verify_evidence() == (True, [])
    config_path = tmp_path / "gate.yaml"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    for command in ("verify-evidence", "export-evidence"):
        assert main([command, "--workspace", str(tmp_path), "--config", str(config_path)]) == 0
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEY", "new-key")
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEYS", "old-key")
    assert AntiShortcutSkill(tmp_path).verify_evidence() == (True, [])


def test_rotation_preserves_updates_after_manager_creation(tmp_path):
    stale = make_state(tmp_path)
    other = make_state(tmp_path)
    other.mark_source_change("latest.py", at_epoch=123)
    stale.rotate_key("new-key")
    reloaded = StateManager(state_path(tmp_path), hmac_key="new-key")
    assert reloaded.get_evidence("last_source_change_at_epoch") == 123


def test_bad_manifest_prevents_state_key_change(tmp_path):
    skill = skill_with_manifest(tmp_path)
    path = skill.evidence_manifest.manifest_file
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"] = {}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = state_path(tmp_path).read_bytes()
    assert main(["rotate-key", "--workspace", str(tmp_path), "--from", "old-key", "--to", "new-key"]) == 1
    assert state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("failure", ["manifest_first", "state", "manifest_last"])
@pytest.mark.parametrize("old_key", ["old-key", None])
def test_interrupted_rotation_is_readable_and_retryable(tmp_path, monkeypatch, failure, old_key):
    skill = skill_with_manifest(tmp_path, old_key)
    manager, manifest = skill.state, skill.evidence_manifest
    original_write = manifest._atomic_write
    calls = 0

    def manifest_write():
        nonlocal calls
        calls += 1
        if (failure == "manifest_first" and calls == 1) or (failure == "manifest_last" and calls == 2):
            raise OSError("simulated interrupted manifest write")
        original_write()

    def state_write():
        raise OSError("simulated interrupted state write")

    with monkeypatch.context() as patch:
        patch.setattr(manifest, "_atomic_write", manifest_write)
        if failure == "state":
            patch.setattr(manager, "_atomic_write", state_write)
        with pytest.raises(OSError):
            rotate_signing_key(manager, manifest, "new-key")

    active_key = "new-key" if failure == "manifest_last" else old_key
    restarted = AntiShortcutSkill(tmp_path, config={"state_hmac_key": active_key})
    assert restarted.verify_evidence() == (True, [])
    # 重启后和原实例上重试都能完成；先在原实例上收尾。
    rotate_signing_key(manager, manifest, "new-key")
    assert main(["rotate-key", "--workspace", str(tmp_path), "--from", "new-key", "--to", "new-key"]) == 0
    assert AntiShortcutSkill(tmp_path, config={"state_hmac_key": "new-key"}).verify_evidence() == (True, [])
    with pytest.raises(EvidenceTamperedError):
        EvidenceManifest(manifest.manifest_file, hmac_key="old-key")


def test_unsigned_rotation_interruption_can_be_retried(tmp_path, monkeypatch):
    skill = skill_with_manifest(tmp_path, None)

    def fail():
        raise OSError("state write failed")

    with monkeypatch.context() as patch:
        patch.setattr(skill.state, "_atomic_write", fail)
        with pytest.raises(OSError):
            rotate_signing_key(skill.state, skill.evidence_manifest, "new-key")
    assert main(["rotate-key", "--workspace", str(tmp_path), "--to", "new-key"]) == 0
    assert AntiShortcutSkill(tmp_path, config={"state_hmac_key": "new-key"}).verify_evidence() == (True, [])


def test_rotation_updates_key_for_manifest_created_later(tmp_path):
    state = make_state(tmp_path)
    manifest = EvidenceManifest(tmp_path / ".agent_gate" / "evidence_manifest.json", hmac_key="old-key")
    rotate_signing_key(state, manifest, "new-key")
    manifest.record(1, {"spec.md": "digest"})
    assert EvidenceManifest(manifest.manifest_file, hmac_key="new-key").entries() == manifest.entries()
