"""状态签名（HMAC-SHA256）测试（v0.8.0）。"""
import json

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.state import StateManager, TamperedStateError
from conftest import SPEC


def make_state(tmp_path, key="secret-key"):
    return StateManager(tmp_path / "state.json", user_request="r", hmac_key=key)


def test_signed_round_trip(tmp_path):
    s = make_state(tmp_path)
    assert s.current_stage == 1
    s.advance(2, {"spec": "ok"})
    reloaded = StateManager(tmp_path / "state.json", hmac_key="secret-key")
    assert reloaded.current_stage == 2


def test_signature_field_written(tmp_path):
    make_state(tmp_path)
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["signature"].startswith("v1:")
    assert len(data["signature"]) == 3 + 64  # v1: + sha256 hex


def test_no_key_no_signature(tmp_path):
    StateManager(tmp_path / "state.json", user_request="r")
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "signature" not in data


def test_tampered_content_rejected(tmp_path):
    make_state(tmp_path)
    p = tmp_path / "state.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["current_stage"] = 6
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TamperedStateError):
        StateManager(p, hmac_key="secret-key")


def test_tampered_signature_rejected(tmp_path):
    make_state(tmp_path)
    p = tmp_path / "state.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["signature"] = "v1:" + "0" * 64
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TamperedStateError):
        StateManager(p, hmac_key="secret-key")


def test_wrong_key_rejected(tmp_path):
    make_state(tmp_path)
    with pytest.raises(TamperedStateError):
        StateManager(tmp_path / "state.json", hmac_key="other-key")


def test_unsigned_file_with_key_rejected(tmp_path):
    StateManager(tmp_path / "state.json", user_request="r")  # 无签名写入
    with pytest.raises(TamperedStateError):
        StateManager(tmp_path / "state.json", hmac_key="secret-key")


def test_env_key_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEY", "env-key")
    s = StateManager(tmp_path / "state.json", user_request="r")
    assert s.current_stage == 1
    StateManager(tmp_path / "state.json")  # 相同 env key 可正常加载
    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEY", "other")
    with pytest.raises(TamperedStateError):
        StateManager(tmp_path / "state.json")


def test_skill_signing_config(tmp_path, fake_tools):
    """验收：config.state_hmac_key 生效，篡改后新实例拒绝加载。"""
    skill = AntiShortcutSkill(
        tmp_path, config={"state_hmac_key": "k"}, user_request="r"
    )
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]

    p = tmp_path / ".agent_gate" / "state.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["current_stage"] = 0
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TamperedStateError):
        AntiShortcutSkill(tmp_path, config={"state_hmac_key": "k"}, user_request="r")


def test_skill_no_key_backward_compatible(tmp_path, fake_tools):
    skill = AntiShortcutSkill(tmp_path, user_request="r")
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    # 无密钥配置的旧行为不受影响
    reloaded = AntiShortcutSkill(tmp_path, user_request="r")
    assert reloaded.current_stage == 2
