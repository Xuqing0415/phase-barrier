"""v0.29.0 插件发现与自动验证测试：入口点验证 / summarize / CLI plugin-verify。"""
from __future__ import annotations

from anti_shortcut.__main__ import main
from anti_shortcut.plugins import (
    PLUGIN_GROUPS,
    _entry_points,
    _verify_entry,
    discover_plugins,
    summarize_plugin_verification,
    verify_language_adapter,
    verify_plugins,
)


class _FakeEP:
    """最小 EntryPoint 替身：name / value / load()。"""

    def __init__(self, name: str, obj, value: str = "pkg:attr"):
        self.name = name
        self.value = value
        self._obj = obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


class GoodAdapter:
    name = "good"

    def check_syntax(self, path):
        return True, "ok"

    def analyze_tests(self, path):
        return {"test_functions": [], "assertions_total": 0}

    def is_source_file(self, path, config=None):
        return False

    def is_test_file(self, path, config=None):
        return False

    def identify_test_command(self, command):
        return False

    def parse_test_output(self, output, exit_code):
        return True, "ok"


def _patch_entry_points(monkeypatch, groups: dict[str, list]):
    """把 anti_shortcut.plugins._entry_points 替换为可控映射。"""
    monkeypatch.setattr(
        "anti_shortcut.plugins._entry_points",
        lambda group: groups.get(group, []),
    )


# ---------- discover / 基础验证 ----------

def test_plugin_groups_defined():
    assert "phase_barrier.languages" in PLUGIN_GROUPS
    assert "phase_barrier.validators" in PLUGIN_GROUPS
    assert "phase_barrier.interceptors" in PLUGIN_GROUPS
    assert "anti_shortcut.integrations" in PLUGIN_GROUPS


def test_discover_plugins(monkeypatch):
    _patch_entry_points(
        monkeypatch,
        {
            "phase_barrier.languages": [
                _FakeEP("foo", GoodAdapter(), "foo_language:FooAdapter")
            ],
            "anti_shortcut.integrations": [
                _FakeEP("hooks", lambda: None, "hooks:install")
            ],
        },
    )
    d = discover_plugins()
    assert d["phase_barrier.languages"][0]["name"] == "foo"
    assert d["anti_shortcut.integrations"][0]["value"] == "hooks:install"


def test_verify_language_adapter_missing_name():
    class NoName(GoodAdapter):
        name = ""

    errors = verify_language_adapter(NoName())
    assert any("name" in e for e in errors)


def test_verify_language_adapter_missing_method():
    class Partial(GoodAdapter):
        analyze_tests = None  # 显式覆盖为不可调用

    partial = Partial()
    errors = verify_language_adapter(partial)
    assert any("analyze_tests" in e for e in errors)


# ---------- verify_plugins：各类插件 ----------

def test_verify_plugins_language_ok_and_broken(monkeypatch):
    class Broken(GoodAdapter):
        name = "broken"
        is_test_file = None  # 显式覆盖为不可调用

    broken = Broken()

    _patch_entry_points(
        monkeypatch,
        {
            "phase_barrier.languages": [
                _FakeEP("good", GoodAdapter()),
                _FakeEP("broken", broken),
                _FakeEP("load_fail", RuntimeError("boom")),
            ]
        },
    )
    r = verify_plugins()
    langs = r["phase_barrier.languages"]
    assert langs["good"]["ok"] is True
    assert langs["broken"]["ok"] is False
    assert any("is_test_file" in e for e in langs["broken"]["errors"])
    assert langs["load_fail"]["ok"] is False
    assert "boom" in langs["load_fail"]["errors"][0]


def test_verify_plugins_validators(monkeypatch):
    def v1(stage=1, **kw):
        return True, "ok"

    v1.stage = 1

    _patch_entry_points(
        monkeypatch,
        {
            "phase_barrier.validators": [
                _FakeEP("mapping", {2: v1}),
                _FakeEP("factory_ok", lambda: {3: v1}),
                _FakeEP("factory_bad", lambda: {}),
                _FakeEP("not_callable", "nope"),
            ]
        },
    )
    r = verify_plugins()
    v = r["phase_barrier.validators"]
    assert v["mapping"]["ok"] is True
    assert v["factory_ok"]["ok"] is True
    assert v["factory_bad"]["ok"] is False
    assert v["not_callable"]["ok"] is False


def test_verify_plugins_interceptors(monkeypatch):
    def rule_a(kind, target, config, stage, content=None):
        return None

    def rule_b(kind, target, config, stage, content=None):
        return None

    class RulesObj:
        rules = [rule_a]

    _patch_entry_points(
        monkeypatch,
        {
            "phase_barrier.interceptors": [
                _FakeEP("rules_list", [rule_a, rule_b]),
                _FakeEP("rules_obj", RulesObj()),
                _FakeEP("empty", []),
            ]
        },
    )
    r = verify_plugins()
    inter = r["phase_barrier.interceptors"]
    assert inter["rules_list"]["ok"] is True
    assert inter["rules_obj"]["ok"] is True
    assert inter["empty"]["ok"] is False


def test_verify_plugins_integrations(monkeypatch):
    def install(skill):
        return {}

    class Installer:
        def install(self, skill):
            return {}

    _patch_entry_points(
        monkeypatch,
        {
            "anti_shortcut.integrations": [
                _FakeEP("fn", install),
                _FakeEP("obj", Installer()),
                _FakeEP("bad", 42),
            ]
        },
    )
    r = verify_plugins()
    integ = r["anti_shortcut.integrations"]
    assert integ["fn"]["ok"] is True
    assert integ["obj"]["ok"] is True
    assert integ["bad"]["ok"] is False


def test_verify_plugins_no_plugins(monkeypatch):
    _patch_entry_points(monkeypatch, {})
    assert verify_plugins() == {}


# ---------- summarize ----------

def test_summarize_all_pass():
    results = {
        "phase_barrier.languages": {
            "foo": {"ok": True, "errors": []},
            "bar": {"ok": True, "errors": []},
        }
    }
    ok, msg = summarize_plugin_verification(results)
    assert ok is True and "2 个插件" in msg


def test_summarize_has_failures():
    results = {
        "phase_barrier.languages": {
            "foo": {"ok": True, "errors": []},
            "bar": {"ok": False, "errors": ["缺少 check_syntax"]},
        }
    }
    ok, msg = summarize_plugin_verification(results)
    assert ok is False
    assert "1/2" in msg
    assert "缺少 check_syntax" in msg


# ---------- CLI plugin-verify ----------

def test_cli_plugin_verify_plain(monkeypatch, capsys):
    monkeypatch.setattr(
        "anti_shortcut.__main__.verify_plugins",
        lambda: {
            "phase_barrier.languages": {
                "foo": {"ok": True, "errors": []}
            }
        },
    )
    monkeypatch.setattr(
        "anti_shortcut.__main__.discover_plugins",
        lambda: {"phase_barrier.languages": [{"name": "foo", "value": "x:y"}]},
    )
    rc = main(["plugin-verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "全部 1 个插件验证通过" in out


def test_cli_plugin_verify_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        "anti_shortcut.__main__.verify_plugins",
        lambda: {
            "phase_barrier.languages": {
                "broken": {"ok": False, "errors": ["缺少 name"]}
            }
        },
    )
    monkeypatch.setattr("anti_shortcut.__main__.discover_plugins", lambda: {})
    rc = main(["plugin-verify"])
    assert rc == 1
    assert "1/1" in capsys.readouterr().out


def test_cli_plugin_verify_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "anti_shortcut.__main__.verify_plugins",
        lambda: {
            "phase_barrier.languages": {
                "foo": {"ok": True, "errors": []}
            }
        },
    )
    monkeypatch.setattr(
        "anti_shortcut.__main__.discover_plugins",
        lambda: {"phase_barrier.languages": [{"name": "foo", "value": "x:y"}]},
    )
    rc = main(["plugin-verify", "--json"])
    import json

    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ok"] is True
    assert data["plugins"]["phase_barrier.languages"]["foo"]["ok"] is True


# ---------- _entry_points 真实调用与兼容回退 ----------

def test_entry_points_real_call():
    # 本地 / CI 已 pip install -e：内置语言适配器已注册，validators 组为空
    langs = _entry_points("phase_barrier.languages")
    assert len(langs) >= 7
    assert "python" in {ep.name for ep in langs}
    assert _entry_points("phase_barrier.validators") == []


def test_entry_points_typeerror_fallback(monkeypatch):
    def legacy_api(*args, **kwargs):
        if kwargs:
            raise TypeError("new API unavailable")
        return {}

    monkeypatch.setattr("anti_shortcut.plugins.metadata.entry_points", legacy_api)
    assert _entry_points("phase_barrier.languages") == []


def test_verify_plugins_real_builtin_languages():
    r = verify_plugins()
    langs = r.get("phase_barrier.languages", {})
    assert "python" in langs
    assert langs["python"]["ok"] is True
    assert langs["python"]["errors"] == []


def test_verify_plugins_validator_factory_raises(monkeypatch):
    def boom_factory():
        raise RuntimeError("config missing")

    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.validators": [_FakeEP("boom", boom_factory)]},
    )
    r = verify_plugins()
    info = r["phase_barrier.validators"]["boom"]
    assert info["ok"] is False
    assert any("RuntimeError" in e and "config missing" in e for e in info["errors"])


def test_verify_plugins_validator_factory_bad_mapping(monkeypatch):
    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.validators": [_FakeEP("badmap", lambda: {1: 42})]},
    )
    r = verify_plugins()
    info = r["phase_barrier.validators"]["badmap"]
    assert info["ok"] is False
    assert any("不可调用" in e for e in info["errors"])


def test_verify_plugins_interceptor_rules_attr_not_list(monkeypatch):
    class BadRules:
        rules = "nope"

    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.interceptors": [_FakeEP("bad_rules", BadRules())]},
    )
    r = verify_plugins()
    info = r["phase_barrier.interceptors"]["bad_rules"]
    assert info["ok"] is False
    assert any("未提供任何可调用规则" in e for e in info["errors"])


def test_verify_plugins_interceptor_dict(monkeypatch):
    def rule_a(kind, target, config, stage, content=None):
        return None

    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.interceptors": [_FakeEP("dict", {"a": rule_a})]},
    )
    r = verify_plugins()
    assert r["phase_barrier.interceptors"]["dict"]["ok"] is True


def test_verify_plugins_interceptor_callable_typeerror(monkeypatch):
    def rule_a(kind, target, config, stage, content=None):
        return None

    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.interceptors": [_FakeEP("fn", rule_a)]},
    )
    r = verify_plugins()
    assert r["phase_barrier.interceptors"]["fn"]["ok"] is True


def test_verify_plugins_interceptor_factory_single_rule(monkeypatch):
    def rule_a(kind, target, config, stage, content=None):
        return None

    def factory():
        return rule_a

    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.interceptors": [_FakeEP("factory_single", factory)]},
    )
    r = verify_plugins()
    assert r["phase_barrier.interceptors"]["factory_single"]["ok"] is True


def test_verify_plugins_validator_mapping_bad_values(monkeypatch):
    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.validators": [_FakeEP("bad_values", {1: 42})]},
    )
    r = verify_plugins()
    info = r["phase_barrier.validators"]["bad_values"]
    assert info["ok"] is False
    assert any("不可调用" in e for e in info["errors"])


def test_verify_plugins_validator_mapping_empty(monkeypatch):
    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.validators": [_FakeEP("empty_map", {})]},
    )
    r = verify_plugins()
    info = r["phase_barrier.validators"]["empty_map"]
    assert info["ok"] is False
    assert any("为空" in e for e in info["errors"])


def test_verify_plugins_validator_with_stage_attr(monkeypatch):
    def v1(stage=1, **kw):
        return True, "ok"

    v1.stage = 1
    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.validators": [_FakeEP("with_stage", v1)]},
    )
    r = verify_plugins()
    assert r["phase_barrier.validators"]["with_stage"]["ok"] is True


def test_verify_plugins_interceptor_bad_obj(monkeypatch):
    _patch_entry_points(
        monkeypatch,
        {"phase_barrier.interceptors": [_FakeEP("bad", 42)]},
    )
    r = verify_plugins()
    info = r["phase_barrier.interceptors"]["bad"]
    assert info["ok"] is False
    assert any("规则" in e for e in info["errors"])


def test_verify_entry_unknown_group():
    ok, errors = _verify_entry("unknown.group", _FakeEP("x", GoodAdapter()))
    assert ok is True
    assert errors == []
