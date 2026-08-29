"""JavaScript/TypeScript 适配器增强测试：tsconfig 语法检查 / jest --listTests / 启发式升级。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import JavaScriptAdapter
from anti_shortcut.languages.base import analyze_js_style_tests, validate_test_collection
from anti_shortcut.validators import validate_tests

TS_OK = "export const x: number = 1;\n"


def _fake_run_factory(rc=0, stdout="", stderr=""):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

    return fake_run


def _patch_which(monkeypatch, names):
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.shutil.which",
        lambda name: names.get(name),
    )


# ---------- TypeScript：tsconfig 优先 ----------

def test_js_ts_uses_project_tsconfig(tmp_path, monkeypatch):
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "fib.ts"
    f.write_text(TS_OK, encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.javascript.subprocess.run", fake_run)
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert ok and "tsc" in msg
    assert "-p" in seen["cmd"]
    assert str(tmp_path / "tsconfig.json") in seen["cmd"]
    assert str(f) not in seen["cmd"]  # -p 模式不允许混用源文件


def test_js_ts_single_file_fallback(tmp_path, monkeypatch):
    f = tmp_path / "fib.ts"
    f.write_text(TS_OK, encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.javascript.subprocess.run", fake_run)
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert ok and "tsc" in msg
    assert "-p" not in seen["cmd"]
    assert seen["cmd"][-1] == str(f)


def test_js_ts_dependency_errors_tolerated(tmp_path, monkeypatch):
    """单文件模式：仅存在模块依赖错误（TS2307）时降级为通过。"""
    f = tmp_path / "fib.ts"
    f.write_text("import { helper } from './helper';\nexport const x = 1;\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(
            rc=1,
            stderr="src/fib.ts:1:10 - error TS2307: Cannot find module './helper' or its corresponding type declarations.\n",
        ),
    )
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert ok and "依赖" in msg


def test_js_ts_syntax_error_rejected(tmp_path, monkeypatch):
    f = tmp_path / "fib.ts"
    f.write_text("export const x: number = ;\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=1, stderr="src/fib.ts:1:22 - error TS1005: ';' expected.\n"),
    )
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert not ok and "TypeScript 语法错误" in msg


def test_js_ts_missing_tsc_message(tmp_path, monkeypatch):
    f = tmp_path / "fib.ts"
    f.write_text(TS_OK, encoding="utf-8")
    _patch_which(monkeypatch, {})
    ok, msg = JavaScriptAdapter().check_syntax(f)
    assert not ok and "TypeScript" in msg


# ---------- jest --listTests 动态发现 ----------

def test_js_jest_configured_missing_jest(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{\"scripts\": {\"test\": \"jest\"}}\n", encoding="utf-8")
    (tmp_path / "fib.test.js").write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=1, stderr="npx: command jest not found\n"),
    )
    cfg = load_config({"language": "javascript", "adapter_options": {"test_discovery": "jest"}})
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok and "jest" in msg


def test_js_jest_configured_lists_tests(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{\"scripts\": {\"test\": \"jest\"}}\n", encoding="utf-8")
    tf = tmp_path / "fib.test.js"
    tf.write_text("test('a', () => { expect(1).toBe(1); });\nit('b', () => { expect(2).toBe(2); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=f"{tf}\n"),
    )
    adapter = JavaScriptAdapter()
    adapter.configure({"test_discovery": "jest"})
    info = adapter.analyze_tests(tf)
    assert info["dynamic"] is True
    assert info["jest_discovered"] == 1

    cfg = load_config({"language": "javascript", "adapter_options": {"test_discovery": "jest"}})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] >= 1


def test_js_jest_auto_probe_when_installed(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "node_modules" / "jest").mkdir(parents=True)
    tf = tmp_path / "fib.test.js"
    tf.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=f"{tf}\n"),
    )
    adapter = JavaScriptAdapter()  # auto
    assert adapter._use_jest_discovery(tf)
    info = adapter.analyze_tests(tf)
    assert info["dynamic"] is True
    assert info["jest_discovered"] == 1


# ---------- 启发式升级 ----------

def test_js_heuristic_counts_each_and_skip():
    info = analyze_js_style_tests(
        "it.each([1, 2])('doubles', (n) => { expect(n * 2).toBe(n * 2); });\n"
        "test.skip('todo', () => { expect(1).toBe(1); });\n"
        "describe.each(['a'])('group', () => { test('x', () => { expect(1).toBe(1); }); });\n"
    )
    # it.each / test.skip / describe.each 均可识别；describe 内嵌的 test 也计数
    assert len(info["test_functions"]) == 4
    assert info["assertions_total"] >= 3


def test_js_heuristic_ignores_comments_and_strings():
    text = (
        "// it('fake test in comment')\n"
        "/* describe('fake block comment') */\n"
        "console.log('test(\"logged\")');\n"
        "test('real', () => { expect(1).toBe(1); });\n"
    )
    info = analyze_js_style_tests(text)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] >= 1


def test_js_validate_test_collection_error_propagated():
    cfg = GateConfig()
    parsed = [
        {
            "file": "fib.test.js",
            "test_functions": [],
            "heuristic": True,
            "assertions_total": 0,
            "error": "已启用 jest --listTests，但未找到 jest",
        }
    ]
    ok, msg, _ = validate_test_collection(cfg, parsed)
    assert not ok and "jest" in msg
# ---------- jest --listTests --json 输出解析 ----------

def test_js_jest_list_tests_json_output(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    tf = tmp_path / "fib.test.js"
    tf.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    import json

    payload = json.dumps({"success": True, "testResults": [{"name": str(tf)}]})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=payload),
    )
    adapter = JavaScriptAdapter()
    adapter.configure({"test_discovery": "jest"})
    info = adapter.analyze_tests(tf)
    assert info["dynamic"] is True
    assert info["jest_discovered"] == 1


def test_js_jest_list_tests_json_with_undefined(tmp_path, monkeypatch):
    """部分 jest 版本的 --json 输出含 undefined（非严格 JSON），应宽松解析。"""
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    tf = tmp_path / "fib.test.js"
    tf.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    payload = '{"success":true,"testResults":[{"name":"%s","coverage":undefined}]}' % str(tf).replace("\\", "\\\\")
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=payload),
    )
    adapter = JavaScriptAdapter()
    adapter.configure({"test_discovery": "jest"})
    info = adapter.analyze_tests(tf)
    assert info["dynamic"] is True
    assert info["jest_discovered"] == 1


def test_js_jest_list_tests_plain_lines_fallback(tmp_path, monkeypatch):
    """旧版 jest 按行输出时仍可解析（--json 兼容回退）。"""
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    tf = tmp_path / "fib.test.js"
    tf.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"npx": "npx"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=f"{tf}\n"),
    )
    adapter = JavaScriptAdapter()
    adapter.configure({"test_discovery": "jest"})
    info = adapter.analyze_tests(tf)
    assert info["dynamic"] is True
    assert info["jest_discovered"] == 1


# ---------- acorn 真实解析 ----------

def test_js_acorn_parsing(tmp_path, monkeypatch):
    f = tmp_path / "fib.test.js"
    f.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"node": "node"})
    payload = '{"acorn":true,"files":[{"path":"fib.test.js","declarations":3,"test_cases":2,"suites":1,"assertions":4}]}'
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=payload),
    )
    info = JavaScriptAdapter().analyze_tests(f)
    assert info["parser"] == "acorn"
    assert len(info["test_functions"]) == 3
    assert info["assertions_total"] == 4


def test_js_acorn_parse_error_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "fib.test.js"
    f.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"node": "node"})
    payload = '{"acorn":true,"files":[{"path":"fib.test.js","error":"Unexpected token"}]}'
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=payload),
    )
    info = JavaScriptAdapter().analyze_tests(f)
    assert info.get("parser") is None  # 回退启发式
    assert len(info["test_functions"]) == 1


def test_js_acorn_missing_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "fib.test.js"
    f.write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"node": "node"})
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout='{"acorn":false,"files":[]}'),
    )
    info = JavaScriptAdapter().analyze_tests(f)
    assert info.get("parser") is None
    assert len(info["test_functions"]) == 1


def test_js_validate_tests_evidence_parsers(tmp_path, monkeypatch):
    """validate_tests 证据中记录真实解析器来源（parsers）。"""
    (tmp_path / "fib.test.js").write_text("test('a', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    _patch_which(monkeypatch, {"node": "node"})
    payload = '{"acorn":true,"files":[{"path":"fib.test.js","declarations":2,"test_cases":2,"suites":0,"assertions":2}]}'
    monkeypatch.setattr(
        "anti_shortcut.languages.javascript.subprocess.run",
        _fake_run_factory(rc=0, stdout=payload),
    )
    cfg = load_config({"language": "javascript"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev.get("parsers") == ["acorn"]