"""自定义拦截规则插件测试（v0.12.0）：进程内注册 / 入口点加载 / Skill 接线。"""
import pytest

import anti_shortcut.interceptors as imod
from anti_shortcut import AntiShortcutSkill, evaluate_rules, register_rule


class FakeEntryPoint:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def load(self):
        return self.value


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = list(imod._rule_registry)
    imod._rule_registry.clear()
    yield
    imod._rule_registry.clear()
    imod._rule_registry.extend(saved)


def test_rule_blocks_write(tmp_path):
    def deny_uploads(kind, target, config, stage):
        # 归一化路径分隔符，兼容 Windows 反斜杠
        norm = str(target).replace("\\", "/")
        if kind == "write" and "uploads/" in norm:
            return False, "禁止写入 uploads/ 目录（自定义规则）"
        return None

    register_rule("deny_uploads", deny_uploads)
    skill = AntiShortcutSkill(tmp_path, user_request="r")
    with pytest.raises(PermissionError, match="uploads/"):
        skill.check_write_permission("uploads/x.txt")


def test_rule_allows_exec_early_test(tmp_path):
    # 阶段 1：内置规则禁止运行 pytest；自定义规则显式放行
    def allow_smoke(kind, target, config, stage):
        if kind == "exec" and "smoke" in target:
            return True, "自定义放行 smoke 命令"
        return None

    register_rule("allow_smoke", allow_smoke)
    skill = AntiShortcutSkill(tmp_path, user_request="r")
    assert skill.current_stage == 1
    skill.check_exec_permission("pytest --smoke")  # 不再被拦截


def test_rule_abstain_falls_back_to_builtin(tmp_path):
    def abstain(kind, target, config, stage):
        return None

    register_rule("abstain", abstain)
    skill = AntiShortcutSkill(tmp_path, user_request="r")
    with pytest.raises(PermissionError):
        skill.check_exec_permission("pytest")  # 阶段 1 内置拦截仍然生效


def test_rule_blocks_gate_dir_even_if_builtin_off(tmp_path):
    # 自定义规则可以扩展内置保护（例如禁止访问 .git）
    def deny_git(kind, target, config, stage):
        if kind == "exec" and ".git" in target:
            return False, "自定义规则禁止访问 .git"
        return None

    register_rule("deny_git", deny_git)
    skill = AntiShortcutSkill(tmp_path, user_request="r")
    with pytest.raises(PermissionError, match=".git"):
        skill.check_exec_permission("cat .git/config")


def test_evaluate_rules_first_decisive_wins(tmp_path):
    from anti_shortcut.config import GateConfig

    def first(kind, target, config, stage):
        return False, "first blocks"

    def second(kind, target, config, stage):
        return True, "second allows"

    register_rule("first", first)
    register_rule("second", second)
    decision, reason = evaluate_rules("write", "x.txt", GateConfig(), 1)
    assert decision is False and "first" in reason


def test_evaluate_rules_all_abstain(tmp_path):
    from anti_shortcut.config import GateConfig

    register_rule("abstain", lambda k, t, c, s: None)
    decision, reason = evaluate_rules("exec", "ls", GateConfig(), 1)
    assert decision is None and reason == ""


def test_evaluate_rules_invalid_kind_raises():
    from anti_shortcut.config import GateConfig

    with pytest.raises(ValueError):
        evaluate_rules("read", "x", GateConfig(), 1)


def test_entry_point_rules_loaded(monkeypatch):
    def rule(kind, target, config, stage):
        if "secrets/" in str(target):
            return False, "入口点规则禁止 secrets/"
        return None

    monkeypatch.setattr(
        imod.metadata,
        "entry_points",
        lambda group=None: [FakeEntryPoint("third_party", lambda: [rule])],
    )
    decision, reason = evaluate_rules("write", "secrets/token.txt", None, 1)
    # 注意：config=None 时规则自行处理；这里规则不访问 config
    assert decision is False and "secrets/" in reason


def test_register_rule_non_callable_raises():
    with pytest.raises(TypeError):
        register_rule("bad", "not-callable")
