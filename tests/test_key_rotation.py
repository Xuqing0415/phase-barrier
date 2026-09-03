"""HMAC 状态签名密钥轮换测试（v0.9.0）。"""
import json

import pytest

from anti_shortcut.__main__ import main
from anti_shortcut.state import StateManager, TamperedStateError


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