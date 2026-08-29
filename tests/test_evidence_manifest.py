"""证据签名清单测试（v0.9.0）。"""
import hashlib
import json

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.__main__ import main
from anti_shortcut.evidence import (
    EVIDENCE_MANIFEST_NAME,
    EvidenceManifest,
    EvidenceTamperedError,
)
from conftest import GOOD_TESTS, SPEC, USER_REQUEST


def sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_manifest(tmp_path, key=None):
    return EvidenceManifest(tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME, hmac_key=key)


def test_record_and_verify_ok(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    m = make_manifest(tmp_path)
    m.record(1, {"spec.md": sha256(spec)})
    ok, violations = m.verify(tmp_path)
    assert ok is True
    assert violations == []


def test_missing_file_detected(tmp_path):
    m = make_manifest(tmp_path)
    m.record(1, {"spec.md": "0" * 64})
    ok, violations = m.verify(tmp_path)
    assert ok is False
    assert any("缺失" in v for v in violations)


def test_tampered_file_detected(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    m = make_manifest(tmp_path)
    m.record(1, {"spec.md": sha256(spec)})
    spec.write_text(SPEC + "\n# 事后篡改", encoding="utf-8")
    ok, violations = m.verify(tmp_path)
    assert ok is False
    assert any("被篡改" in v for v in violations)


def test_signed_manifest_rejects_tamper(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    m = make_manifest(tmp_path, key="k")
    m.record(1, {"spec.md": sha256(spec)})
    mf = tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME
    data = json.loads(mf.read_text(encoding="utf-8"))
    data["entries"]["spec.md"]["sha256"] = "0" * 64
    mf.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvidenceTamperedError):
        EvidenceManifest(mf, hmac_key="k")


def test_unsigned_manifest_with_key_rejected(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    m = make_manifest(tmp_path)  # 无密钥：清单未签名
    m.record(1, {"spec.md": sha256(spec)})
    with pytest.raises(EvidenceTamperedError):
        EvidenceManifest(tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME, hmac_key="k")


def test_skill_records_spec_evidence(tmp_path, fake_tools):
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    mf = tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME
    assert mf.exists()
    manifest = EvidenceManifest(mf)
    entries = manifest.entries()
    assert entries["spec.md"]["stage"] == 1
    ok, violations = manifest.verify(tmp_path)
    assert ok is True


def test_skill_full_flow_signs_all_evidence(tmp_path, fake_tools):
    """验收：spec / 测试 / 实现三阶段证据全部进入签名清单。"""
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", "def fib(n):\n    return n\n")
    assert tools["advance_stage"](4)["success"]
    manifest = EvidenceManifest(tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME)
    entries = manifest.entries()
    assert "spec.md" in entries
    assert "test_fib.py" in entries
    assert "fib.py" in entries


def test_skill_signed_manifest_with_hmac(tmp_path, fake_tools):
    skill = AntiShortcutSkill(
        tmp_path, config={"state_hmac_key": "k"}, user_request=USER_REQUEST
    )
    tools = skill.install(fake_tools)
    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    manifest = EvidenceManifest(
        tmp_path / ".agent_gate" / EVIDENCE_MANIFEST_NAME, hmac_key="k"
    )
    assert manifest.is_signed()
    ok, _ = manifest.verify(tmp_path)
    assert ok is True


def test_cli_verify_evidence_ok(capsys, tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    assert skill.advance_stage(2)["success"]
    rc = main(["verify-evidence", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out and "spec.md" in out


def test_cli_verify_evidence_detects_tamper(capsys, tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    assert skill.advance_stage(2)["success"]
    spec.write_text(SPEC + "\n# 篡改", encoding="utf-8")
    rc = main(["verify-evidence", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "被篡改" in err


def test_cli_verify_evidence_empty_manifest(capsys, tmp_path):
    rc = main(["verify-evidence", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "清单为空" in err


# ---------- v0.10.0：export-evidence ----------

def test_cli_export_evidence(capsys, tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    assert skill.advance_stage(2)["success"]
    out_path = tmp_path / "bundle.json"
    rc = main(["export-evidence", "--workspace", str(tmp_path), "--out", str(out_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "已导出" in out
    bundle = json.loads(out_path.read_text(encoding="utf-8"))
    assert bundle["verified"] is True
    assert bundle["signed"] is False
    assert "spec.md" in bundle["files"]
    assert bundle["files"]["spec.md"]["stage"] == 1
    assert "sha256" in bundle["files"]["spec.md"]


def test_cli_export_evidence_tampered(capsys, tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    assert skill.advance_stage(2)["success"]
    spec.write_text(SPEC + "\n# 篡改", encoding="utf-8")
    rc = main(["export-evidence", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    bundle = json.loads(out)
    assert bundle["verified"] is False
    assert any("被篡改" in v for v in bundle["violations"])
