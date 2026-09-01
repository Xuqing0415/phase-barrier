"""内置安全规则包测试（v0.26.0）：config.rules 启用 / 拦截行为 / Skill 接线。"""
import pytest

from anti_shortcut import (
    AntiShortcutSkill,
    BUILTIN_RULES,
    RULE_DESCRIPTIONS,
    GateConfig,
    evaluate_rules,
)


def test_builtin_rules_registry():
    assert set(BUILTIN_RULES) == {
        "no_shell_injection",
        "no_path_traversal",
        "no_hardcoded_secrets",
        "require_license_header",
    }
    for name, desc in RULE_DESCRIPTIONS.items():
        assert name in BUILTIN_RULES and desc.strip()


def test_rules_disabled_by_default():
    decision, reason = evaluate_rules("write", "../../etc/passwd", GateConfig(), 1)
    assert decision is None and reason == ""


# ---------- no_path_traversal ----------

def test_no_path_traversal_blocks_escape(tmp_path):
    cfg = GateConfig(rules=["no_path_traversal"], workspace=tmp_path)
    decision, reason = evaluate_rules("write", "../../etc/passwd", cfg, 1)
    assert decision is False and "no_path_traversal" in reason


def test_no_path_traversal_blocks_absolute_outside(tmp_path):
    cfg = GateConfig(rules=["no_path_traversal"], workspace=tmp_path)
    outside = tmp_path.parent / "outside.txt"
    decision, reason = evaluate_rules("write", str(outside), cfg, 1)
    assert decision is False


def test_no_path_traversal_allows_inside(tmp_path):
    cfg = GateConfig(rules=["no_path_traversal"], workspace=tmp_path)
    decision, _ = evaluate_rules("write", "src/x.py", cfg, 1)
    assert decision is None


# ---------- no_shell_injection ----------

@pytest.mark.parametrize(
    "command",
    [
        "pytest; rm -rf /",
        "pytest && ls",
        "make test || echo boom",
        "echo $(whoami)",
        "x=$(cat secret.txt)",
        "echo `whoami`",
    ],
)
def test_no_shell_injection_blocks(command):
    cfg = GateConfig(rules=["no_shell_injection"])
    decision, reason = evaluate_rules("exec", command, cfg, 3)
    assert decision is False and "no_shell_injection" in reason


@pytest.mark.parametrize(
    "command",
    [
        "python -c 'import sys; print(sys.version)'",
        "pytest | tee out.txt",
        "ls -la",
        "go test ./...",
    ],
)
def test_no_shell_injection_allows_benign(command):
    cfg = GateConfig(rules=["no_shell_injection"])
    decision, _ = evaluate_rules("exec", command, cfg, 3)
    assert decision is None


def test_no_shell_injection_ignores_write_kind():
    cfg = GateConfig(rules=["no_shell_injection"])
    decision, _ = evaluate_rules("write", "a;b", cfg, 3)
    assert decision is None


# ---------- no_hardcoded_secrets ----------

def test_no_hardcoded_secrets_blocks_api_key():
    cfg = GateConfig(rules=["no_hardcoded_secrets"])
    decision, reason = evaluate_rules(
        "write", "config.py", cfg, 1, content='api_key = "sk-live-abcdefgh123456"'
    )
    assert decision is False and "no_hardcoded_secrets" in reason


def test_no_hardcoded_secrets_blocks_private_key():
    cfg = GateConfig(rules=["no_hardcoded_secrets"])
    decision, _ = evaluate_rules(
        "write", "key.pem", cfg, 1,
        content="-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    )
    assert decision is False


def test_no_hardcoded_secrets_allows_clean():
    cfg = GateConfig(rules=["no_hardcoded_secrets"])
    decision, _ = evaluate_rules("write", "config.py", cfg, 1, content="x = 1")
    assert decision is None


def test_no_hardcoded_secrets_skips_without_content():
    cfg = GateConfig(rules=["no_hardcoded_secrets"])
    decision, _ = evaluate_rules("write", "config.py", cfg, 1)
    assert decision is None


# ---------- require_license_header ----------

def test_require_license_header_blocks_missing():
    cfg = GateConfig(
        rules=["require_license_header"],
        rules_options={"license_header": "Copyright (c) 2026 Example"},
    )
    decision, reason = evaluate_rules("write", "main.py", cfg, 1, content="print(1)")
    assert decision is False and "license_header" in reason


def test_require_license_header_passes_with_header():
    cfg = GateConfig(
        rules=["require_license_header"],
        rules_options={"license_header": "Copyright (c) 2026 Example"},
    )
    decision, _ = evaluate_rules(
        "write", "main.py", cfg, 1, content="# Copyright (c) 2026 Example\nprint(1)"
    )
    assert decision is None


def test_require_license_header_passes_shebang_before_header():
    cfg = GateConfig(
        rules=["require_license_header"],
        rules_options={"license_header": "Copyright (c) 2026 Example"},
    )
    decision, _ = evaluate_rules(
        "write", "main.py", cfg, 1,
        content="#!/usr/bin/env python\n# Copyright (c) 2026 Example\nprint(1)",
    )
    assert decision is None


def test_require_license_header_abstains_without_option():
    cfg = GateConfig(rules=["require_license_header"])
    decision, _ = evaluate_rules("write", "main.py", cfg, 1, content="print(1)")
    assert decision is None


# ---------- Skill 接线：内容透传 ----------

def test_skill_blocks_secret_via_wrap_write_file(tmp_path):
    cfg = GateConfig(rules=["no_hardcoded_secrets"])
    skill = AntiShortcutSkill(tmp_path, config=cfg, user_request="r")

    def real_write(path, content):
        (tmp_path / path).write_text(content, encoding="utf-8")
        return True

    tools = skill.install({"write_file": real_write})
    with pytest.raises(PermissionError, match="no_hardcoded_secrets"):
        tools["write_file"]("config.py", 'password = "hunter2-secret-value"')

    assert skill.current_stage == 1  # 未写任何文件，阶段不变


def test_skill_allows_clean_write_via_wrap_write_file(tmp_path):
    cfg = GateConfig(rules=["no_hardcoded_secrets", "no_path_traversal"])
    skill = AntiShortcutSkill(tmp_path, config=cfg, user_request="r")

    def real_write(path, content):
        (tmp_path / path).write_text(content, encoding="utf-8")
        return True

    tools = skill.install({"write_file": real_write})
    tools["write_file"]("README.md", "clean docs\n")
    assert (tmp_path / "README.md").exists()
