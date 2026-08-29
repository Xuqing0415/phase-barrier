"""v0.3.0 语言适配层测试：注册表、自动检测、适配器选择、Python/JS 适配器、Skill 接线。"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill, load_config
from anti_shortcut.config import GateConfig
from anti_shortcut.interceptors import is_language_test_command
from anti_shortcut.languages import (
    LANGUAGE_REGISTRY,
    JavaScriptAdapter,
    LanguageAdapter,
    PythonAdapter,
    detect_language,
    get_adapter,
)
from anti_shortcut.languages.base import analyze_js_style_tests, validate_test_collection
from anti_shortcut.validators import validate_implementation, validate_tests
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST

JS_TESTS = """import { fib } from './src/fib';

test('fib(3) is 2', () => { expect(fib(3)).toBe(2); });
it('fib(10) is 55', () => { expect(fib(10)).toBe(55); });
"""


def _inject_module(monkeypatch, name: str, cls) -> types.ModuleType:
    mod = types.ModuleType(name)
    setattr(mod, cls.__name__, cls)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


# ---------- 注册表 / 自动检测 ----------

def test_language_registry_builtins():
    assert "python" in LANGUAGE_REGISTRY
    assert "javascript" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["python"] is PythonAdapter
    assert LANGUAGE_REGISTRY["javascript"] is JavaScriptAdapter


def test_detect_language_python_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    assert detect_language(tmp_path) == "python"


def test_detect_language_python_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert detect_language(tmp_path) == "python"


def test_detect_language_javascript(tmp_path):
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    assert detect_language(tmp_path) == "javascript"


def test_detect_language_java_go_rust(tmp_path):
    (tmp_path / "java").mkdir()
    (tmp_path / "java" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    assert detect_language(tmp_path / "java") == "java"
    (tmp_path / "go").mkdir()
    (tmp_path / "go" / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect_language(tmp_path / "go") == "go"
    (tmp_path / "rust").mkdir()
    (tmp_path / "rust" / "Cargo.toml").write_text("", encoding="utf-8")
    assert detect_language(tmp_path / "rust") == "rust"


def test_detect_language_default_python(tmp_path):
    assert detect_language(tmp_path) == "python"


# ---------- get_adapter 选择逻辑 ----------

def test_get_adapter_default_python(tmp_path):
    adapter = get_adapter(GateConfig(), tmp_path)
    assert isinstance(adapter, PythonAdapter)


def test_get_adapter_auto_detect_javascript(tmp_path):
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    adapter = get_adapter(GateConfig(), tmp_path)
    assert isinstance(adapter, JavaScriptAdapter)


def test_get_adapter_explicit_javascript(tmp_path):
    cfg = load_config({"language": "javascript"})
    assert isinstance(get_adapter(cfg, tmp_path), JavaScriptAdapter)


def test_get_adapter_unknown_language_raises(tmp_path):
    cfg = load_config({"language": "brainfuck"})
    with pytest.raises(ValueError, match="未知语言"):
        get_adapter(cfg, tmp_path)


def test_get_adapter_custom_module(tmp_path, monkeypatch):
    class DummyAdapter(LanguageAdapter):
        name = "dummy"

        def check_syntax(self, path):
            return True, "ok"

    _inject_module(monkeypatch, "dummy_adapters", DummyAdapter)
    cfg = load_config({"language_adapter": "dummy_adapters.DummyAdapter"})
    assert isinstance(get_adapter(cfg, tmp_path), DummyAdapter)


def test_get_adapter_custom_module_configure(tmp_path, monkeypatch):
    seen: dict = {}

    class ConfigurableAdapter(LanguageAdapter):
        name = "cfg"

        def configure(self, options):
            seen.update(options)

        def check_syntax(self, path):
            return True, "ok"

    _inject_module(monkeypatch, "cfg_adapters", ConfigurableAdapter)
    cfg = load_config(
        {
            "language_adapter": "cfg_adapters.ConfigurableAdapter",
            "adapter_options": {"min": 5},
        }
    )
    get_adapter(cfg, tmp_path)
    assert seen == {"min": 5}


def test_get_adapter_custom_module_factory(tmp_path, monkeypatch):
    """入口点形式的适配器也可以是返回实例的工厂函数。"""

    def make_adapter():
        return PythonAdapter()

    mod = types.ModuleType("factory_adapters")
    mod.make_adapter = make_adapter
    monkeypatch.setitem(sys.modules, "factory_adapters", mod)
    cfg = load_config({"language_adapter": "factory_adapters.make_adapter"})
    assert isinstance(get_adapter(cfg, tmp_path), PythonAdapter)


def test_get_adapter_custom_bad_path_raises(tmp_path):
    cfg = load_config({"language_adapter": "no.such.module.Adapter"})
    with pytest.raises(ValueError, match="无法导入"):
        get_adapter(cfg, tmp_path)


# ---------- PythonAdapter 回归 ----------

def test_python_adapter_file_classification():
    adapter = PythonAdapter()
    assert adapter.is_source_file("fib.py")
    assert not adapter.is_test_file("fib.py")
    assert adapter.is_test_file("test_fib.py")
    assert adapter.is_test_file("tests/test_fib.py")
    assert not adapter.is_source_file("test_fib.py")
    assert not adapter.is_source_file("spec.md")


def test_python_adapter_check_syntax(tmp_path):
    adapter = PythonAdapter()
    good = tmp_path / "fib.py"
    good.write_text(GOOD_IMPL, encoding="utf-8")
    ok, _ = adapter.check_syntax(good)
    assert ok
    bad = tmp_path / "bad.py"
    bad.write_text("def fib(:\n", encoding="utf-8")
    ok, msg = adapter.check_syntax(bad)
    assert not ok and "语法错误" in msg


def test_python_adapter_analyze_tests(tmp_path):
    adapter = PythonAdapter()
    p = tmp_path / "test_fib.py"
    p.write_text(GOOD_TESTS, encoding="utf-8")
    info = adapter.analyze_tests(p)
    assert info is not None
    assert len(info["test_functions"]) == 3
    assert all(t["assertions"] >= 1 for t in info["test_functions"])
    bad = tmp_path / "test_bad.py"
    bad.write_text("def test_x(:\n", encoding="utf-8")
    assert adapter.analyze_tests(bad) is None


# ---------- JavaScriptAdapter ----------

def test_js_adapter_file_classification():
    adapter = JavaScriptAdapter()
    assert adapter.is_source_file("src/fib.ts")
    assert adapter.is_source_file("fib.js")
    assert adapter.is_test_file("fib.test.js")
    assert adapter.is_test_file("fib.spec.ts")
    assert adapter.is_test_file("__tests__/x.js")
    assert not adapter.is_source_file("fib.test.js")
    assert not adapter.is_source_file("spec.md")


def test_js_adapter_analyze_tests_heuristic(tmp_path):
    f = tmp_path / "fib.test.js"
    f.write_text(JS_TESTS, encoding="utf-8")
    info = JavaScriptAdapter().analyze_tests(f)
    assert info["heuristic"] is True
    assert len(info["test_functions"]) == 2
    assert info["assertions_total"] >= 2


def test_analyze_js_style_tests():
    info = analyze_js_style_tests(
        "test('a', () => { expect(1).toBe(1); })\n"
        "it('b', () => { assert(2); })\n"
    )
    assert len(info["test_functions"]) == 2
    assert info["assertions_total"] >= 2


def test_js_adapter_identify_test_command():
    adapter = JavaScriptAdapter()
    assert adapter.identify_test_command("npm test")
    assert adapter.identify_test_command("npx jest --runInBand")
    assert adapter.identify_test_command("yarn test")
    assert adapter.identify_test_command("npx tsc --noEmit")
    assert not adapter.identify_test_command("npm run build")
    assert not adapter.identify_test_command("ls -la")


def test_js_adapter_check_syntax_missing_node(tmp_path, monkeypatch):
    f = tmp_path / "fib.js"
    f.write_text("export const x = 1;\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.javascript.shutil.which", lambda name: None)
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert not ok and "Node.js" in msg


def test_js_adapter_check_syntax_ok_mocked(tmp_path, monkeypatch):
    f = tmp_path / "fib.js"
    f.write_text("export const x = 1;\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.javascript.shutil.which", lambda name: "node")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.javascript.subprocess.run", fake_run)
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert ok


def test_js_adapter_ts_missing_tsc(tmp_path, monkeypatch):
    f = tmp_path / "fib.ts"
    f.write_text("export const x: number = 1;\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.javascript.shutil.which", lambda name: None)
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert not ok and "TypeScript" in msg


# ---------- 共享校验策略 / 命令识别 ----------

def test_validate_test_collection_heuristic_empty():
    cfg = GateConfig()
    parsed = [{"file": "fib.test.js", "test_functions": [], "heuristic": True, "assertions_total": 0}]
    ok, msg, _ = validate_test_collection(cfg, parsed)
    assert not ok and "空壳" in msg


def test_validate_test_collection_count_insufficient():
    cfg = GateConfig()
    parsed = [{"file": "test_a.py", "test_functions": [{"name": "test_a", "assertions": 1}]}]
    ok, msg, _ = validate_test_collection(cfg, parsed)
    assert not ok and "测试函数数量不足" in msg


def test_is_language_test_command_precedence():
    cfg = GateConfig()
    adapter = JavaScriptAdapter()
    assert is_language_test_command("npm test", cfg, adapter)          # 适配器规则
    assert is_language_test_command("pytest", cfg, adapter)            # 配置正则兜底
    assert is_language_test_command("make test", cfg, adapter)         # 关键词兜底
    assert not is_language_test_command("npm run build", cfg, adapter)
    assert not is_language_test_command("ls -la", cfg, adapter)
    assert not is_language_test_command("", cfg, adapter)


# ---------- 校验器 + Skill 与 JS 适配器接线 ----------

def test_validate_tests_javascript_with_language_config(tmp_path):
    (tmp_path / "fib.test.js").write_text(JS_TESTS, encoding="utf-8")
    cfg = load_config({"language": "javascript"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] == 2


def test_validate_tests_javascript_empty_rejected(tmp_path):
    (tmp_path / "fib.test.js").write_text("// nothing here\n", encoding="utf-8")
    cfg = load_config({"language": "javascript"})
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok and "空壳" in msg


def test_validate_implementation_js_node_missing(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "fib.js").write_text("export function fib(n) { return n; }\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.javascript.shutil.which", lambda name: None)
    cfg = load_config({"language": "javascript"})
    ok, msg, _ = validate_implementation(tmp_path, cfg, None)
    assert not ok and "Node.js" in msg


def test_js_test_command_blocked_before_impl(tmp_path, fake_tools):
    skill = AntiShortcutSkill(tmp_path, config={"language": "javascript"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("npm test")


def test_js_source_write_blocked_before_tests(tmp_path, fake_tools):
    skill = AntiShortcutSkill(tmp_path, config={"language": "javascript"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="测试用例"):
        tools["write_file"]("src/fib.js", "export function fib(n) { return n; }\n")


def test_js_test_write_blocked_before_spec(tmp_path, fake_tools):
    skill = AntiShortcutSkill(tmp_path, config={"language": "javascript"}, user_request=USER_REQUEST)
    skill.state._data["current_stage"] = 0
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="spec"):
        tools["write_file"]("fib.test.js", JS_TESTS)


def test_skill_javascript_full_flow(tmp_path, monkeypatch, fake_tools):
    """验收：language: javascript 时阶段校验与工具拦截生效，可完整走通交付。"""
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.shutil.which",
        lambda name: {"node": "node"}.get(name),
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.javascript.subprocess.run", fake_run)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")

    skill = AntiShortcutSkill(tmp_path, config={"language": "javascript"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    assert isinstance(skill.adapter, JavaScriptAdapter)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("src/fib.test.js", JS_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("src/fib.js", "export function fib(n) { return n; }\n")
    assert tools["advance_stage"](4)["success"]
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "summary": "2 passed"})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete


def test_skill_custom_adapter_via_language_adapter(tmp_path, fake_tools, monkeypatch):
    """验收：自定义适配器可通过 language_adapter 配置加载并参与校验。"""

    class VerbatimAdapter(LanguageAdapter):
        name = "verbatim"

        def check_syntax(self, path):
            return True, "ok"

    _inject_module(monkeypatch, "verbatim_adapters", VerbatimAdapter)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    skill = AntiShortcutSkill(
        tmp_path,
        config={
            "language_adapter": "verbatim_adapters.VerbatimAdapter",
            "test_file_patterns": ["*.test.js"],
            "source_file_patterns": ["*.js"],
        },
        user_request=USER_REQUEST,
    )
    assert skill.adapter.name == "verbatim"
