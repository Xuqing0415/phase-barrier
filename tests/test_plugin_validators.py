"""自定义校验器插件测试（v0.12.0）：进程内注册 / 入口点加载 / Skill 接线。"""
import pytest

import anti_shortcut.validators as vmod
from anti_shortcut import AntiShortcutSkill, get_validator, register_validator
from conftest import SPEC, USER_REQUEST


class FakeEntryPoint:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def load(self):
        return self.value


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(vmod._custom_validators)
    vmod._custom_validators.clear()
    yield
    vmod._custom_validators.clear()
    vmod._custom_validators.update(saved)


def test_register_and_get_custom_validator():
    def custom(workspace, config, state, adapter=None):
        return True, "自定义校验通过", {"custom": True}

    register_validator(1, custom)
    assert get_validator(1) is custom


def test_custom_validator_overrides_builtin(tmp_path):
    # 无 spec 的工作区：内置校验器会拒绝，自定义校验器放行
    def custom(workspace, config, state, adapter=None):
        return True, "自定义校验通过", {"custom": True}

    register_validator(1, custom)
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    result = skill.advance_stage(2)
    assert result["success"] is True
    assert result["evidence"].get("custom") is True


def test_custom_validator_blocking_reason(tmp_path):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")

    def custom(workspace, config, state, adapter=None):
        return False, "自定义门禁：必须额外提供 design-review.md", {}

    register_validator(1, custom)
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    result = skill.advance_stage(2)
    assert result["success"] is False
    assert "design-review.md" in result["error"]


def test_entry_point_dict_mapping(monkeypatch):
    def custom(workspace, config, state, adapter=None):
        return False, "来自入口点的校验器", {}

    monkeypatch.setattr(
        vmod.metadata,
        "entry_points",
        lambda group=None: [FakeEntryPoint("third_party", {1: custom})],
    )
    assert get_validator(1) is custom


def test_entry_point_factory(monkeypatch):
    def custom(workspace, config, state, adapter=None):
        return True, "工厂校验器", {"via": "factory"}

    monkeypatch.setattr(
        vmod.metadata,
        "entry_points",
        lambda group=None: [FakeEntryPoint("factory", lambda: {2: custom})],
    )
    assert get_validator(2) is custom


def test_entry_point_single_validator_with_stage(monkeypatch):
    def custom(workspace, config, state, adapter=None):
        return True, "单阶段校验器", {}

    custom.stage = 3
    monkeypatch.setattr(
        vmod.metadata,
        "entry_points",
        lambda group=None: [FakeEntryPoint("single", custom)],
    )
    assert get_validator(3) is custom


def test_entry_point_error_skipped(monkeypatch):
    class Boom:
        def load(self):
            raise RuntimeError("broken plugin")

    monkeypatch.setattr(
        vmod.metadata, "entry_points", lambda group=None: [Boom()]
    )
    # 加载失败不影响内置校验器
    assert get_validator(1).__name__ == "validate_spec"


def test_register_invalid_stage_raises():
    with pytest.raises(ValueError):
        register_validator(-1, lambda *a, **k: (True, "", {}))


def test_register_non_callable_raises():
    with pytest.raises(TypeError):
        register_validator(1, "not-callable")
