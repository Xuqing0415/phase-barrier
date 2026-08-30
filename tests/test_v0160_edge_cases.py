# -*- coding: utf-8 -*-
"""v0.16.0 边界补强测试：输出解析（ANSI / 千分位 / 多语言格式）与覆盖率门禁临界、sidecar CLI。"""
import argparse
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from anti_shortcut.config import GateConfig
from anti_shortcut.interceptors import summarize_test_output
from anti_shortcut.languages import GoAdapter, JavaAdapter, JavaScriptAdapter, RubyAdapter, RustAdapter
from anti_shortcut.sidecar import GateSidecar, _merge_config, main
from anti_shortcut.state import StateManager
from anti_shortcut.validators import validate_retest, validate_test_run


def make_state(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "state.json", user_request="r")


# ---------- 输出解析：ANSI 与异常输入 ----------

def test_summarize_ansi_color_codes():
    out = "\x1b[32m3 passed\x1b[0m\n\x1b[36mTOTAL\x1b[0m        5      0   100%"
    rec = summarize_test_output(out, 0)
    assert rec["passed"] is True
    assert rec["coverage"] == 100.0
    assert "3 passed" in rec["summary"]


def test_summarize_output_tail_truncated():
    long_out = "\n".join(f"line {i}" for i in range(500))
    rec = summarize_test_output(long_out, 0, max_tail=200)
    assert rec["passed"] is True
    assert len(rec["output_tail"]) <= 200


def test_summarize_coverage_na_ignored():
    rec = summarize_test_output("coverage: N/A\n3 passed", 0)
    assert rec["coverage"] is None
    assert rec["passed"] is True


def test_summarize_go_coverage_100_float():
    rec = summarize_test_output("ok  pkg/fib  coverage: 100.0% of statements", 0)
    assert rec["coverage"] == 100.0


def test_summarize_istanbul_thousands():
    rec = summarize_test_output(
        "File      | % Stmts |\n----------|---------|\n"
        "All files |   1,234 |\n",
        0,
    )
    assert rec["coverage"] == 1234.0


def test_summarize_pytest_cov_mixed_output():
    out = (
        "======================== test session starts ========================\n"
        "fib.py    5      0   100%\n"
        "TOTAL     5      0   100%\n"
        "========================= 3 passed in 0.05s =========================\n"
    )
    rec = summarize_test_output(out, 0)
    assert rec["passed"] is True
    assert rec["coverage"] == 100.0
    assert "3 passed" in rec["summary"]


# ---------- 输出解析：多语言格式边界 ----------

def test_java_surefire_skipped_zero():
    a = JavaAdapter()
    ok, summary = a.parse_test_output(
        "Tests run: 5, Failures: 0, Errors: 0, Skipped: 0", 0
    )
    assert ok and "Tests run: 5" in summary and "Skipped: 0" in summary


def test_java_gradle_failure_agg():
    a = JavaAdapter()
    ok, summary = a.parse_test_output(
        "3 tests completed, 1 failed", 1
    )
    assert not ok and "3 tests completed, 1 failed" in summary


def test_java_junit_console_success():
    a = JavaAdapter()
    ok, summary = a.parse_test_output("[ 3 tests successful ]", 0)
    assert ok and "[ 3 tests successful ]" in summary


def test_go_mixed_ok_fail_lines():
    a = GoAdapter()
    ok, summary = a.parse_test_output(
        "=== RUN   TestFibBasic\n--- PASS: TestFibBasic\n"
        "=== RUN   TestFibNegative\n--- FAIL: TestFibNegative\n"
        "FAIL\tpkg/fib\t0.6s\n",
        1,
    )
    assert not ok and "-- FAIL" in summary and "TestFibNegative" in summary


def test_rust_ok_without_failures_block():
    a = RustAdapter()
    ok, summary = a.parse_test_output(
        "running 5 tests\n\ntest result: ok. 5 passed; 0 failed", 0
    )
    assert ok and "5 passed; 0 failed" in summary


def test_rust_compile_error_detected():
    a = RustAdapter()
    ok, summary = a.parse_test_output(
        "error[E0425]: cannot find value `fib` in this scope", 1
    )
    assert not ok and "编译失败" in summary


def test_js_vitest_pass_summary():
    a = JavaScriptAdapter()
    ok, summary = a.parse_test_output("Tests: 5 passed, 0 failed\nTest Files: 1 passed", 0)
    assert ok and "5 passed" in summary


def test_js_playwright_fail_detected():
    a = JavaScriptAdapter()
    ok, summary = a.parse_test_output(
        "Running 2 tests\nFAIL tests/a.spec.js\n2 failed", 1
    )
    assert not ok and "failed" in summary


def test_js_empty_output_exit_codes():
    a = JavaScriptAdapter()
    ok, _ = a.parse_test_output("", 0)
    assert ok
    ok2, _ = a.parse_test_output("", 1)
    assert not ok2


# ---------- 覆盖率门禁临界 ----------

def test_cov_threshold_exact_equal(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 80.0})
    ok, _, _ = validate_test_run(tmp_path, cfg, s)
    assert ok


def test_cov_threshold_just_below(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True, "coverage": 79.9})
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert not ok and "覆盖率不足" in msg and "79.9" in msg


def test_cov_retest_missing_report(tmp_path):
    cfg = GateConfig(coverage_threshold=80)
    s = make_state(tmp_path)
    s.mark_source_change("fib.py")
    s.mark_test_run({"exit_code": 0, "passed": True})
    ok, msg, _ = validate_retest(tmp_path, cfg, s)
    assert not ok and "覆盖率" in msg


def test_config_rejects_invalid_threshold():
    with pytest.raises(ValidationError):
        GateConfig(coverage_threshold=150)
    with pytest.raises(ValidationError):
        GateConfig(coverage_threshold=-1)


# ---------- sidecar CLI 边界（v0.16.0） ----------

def _fake_server_cls():
    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr
            self.handler = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    return FakeServer


def test_sidecar_main_starts_and_stops(tmp_path, monkeypatch, capsys):
    import anti_shortcut.sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "ThreadingHTTPServer", _fake_server_cls())
    rc = main(["--workspace", str(tmp_path), "--port", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "phase-barrier" in out and "http://0.0.0.0:0" in out and "1" in out


def test_sidecar_main_sets_state_key_env(tmp_path, monkeypatch):
    import anti_shortcut.sidecar as sidecar_mod

    monkeypatch.setenv("PHASE_BARRIER_HMAC_KEY", "")
    monkeypatch.delenv("PHASE_BARRIER_HMAC_KEY", raising=False)
    monkeypatch.setattr(sidecar_mod, "ThreadingHTTPServer", _fake_server_cls())
    main(["--workspace", str(tmp_path), "--state-key", "s3cret", "--port", "0"])
    import os
    assert os.environ.get("PHASE_BARRIER_HMAC_KEY") == "s3cret"


def test_sidecar_merge_config_env_precedence(monkeypatch):
    args = argparse.Namespace(
        config=None,
        audit_remote_url="",
        audit_remote_token="",
        audit_remote_client_cert="",
        audit_remote_client_key="",
        audit_remote_spool_dir="",
        audit_remote_headers=[],
    )
    monkeypatch.setenv("AUDIT_REMOTE_URL", "https://env.example")
    monkeypatch.setenv("AUDIT_REMOTE_HEADERS", '{"X-Env": "1"}')
    cfg = _merge_config(args)
    assert cfg["audit_remote_url"] == "https://env.example"
    assert cfg["audit_remote_headers"] == {"X-Env": "1"}


def test_sidecar_merge_config_cli_over_env(monkeypatch):
    args = argparse.Namespace(
        config=None,
        audit_remote_url="https://cli.example",
        audit_remote_token="t",
        audit_remote_client_cert="",
        audit_remote_client_key="",
        audit_remote_spool_dir="",
        audit_remote_headers=["X-Cli=1"],
    )
    monkeypatch.setenv("AUDIT_REMOTE_URL", "https://env.example")
    monkeypatch.setenv("AUDIT_REMOTE_HEADERS", "not-json")
    cfg = _merge_config(args)
    assert cfg["audit_remote_url"] == "https://cli.example"
    assert cfg["audit_remote_headers"] == {"X-Cli": "1"}


def test_sidecar_merge_config_from_yaml(monkeypatch, tmp_path):
    cfg_file = tmp_path / "gate.yaml"
    cfg_file.write_text("coverage_threshold: 85\n", encoding="utf-8")
    args = argparse.Namespace(
        config=str(cfg_file),
        audit_remote_url="",
        audit_remote_token="",
        audit_remote_client_cert="",
        audit_remote_client_key="",
        audit_remote_spool_dir="",
        audit_remote_headers=[],
    )
    monkeypatch.setenv("AUDIT_REMOTE_URL", "https://env.example")
    cfg = _merge_config(args)
    assert cfg.coverage_threshold == 85
    assert cfg.audit_remote_url == "https://env.example"

# ---------- 真实工具路径的确定性覆盖（mock subprocess，不依赖工具链） ----------

def test_ruby_check_syntax_mock_ok(tmp_path, monkeypatch):
    f = tmp_path / "fib.rb"
    f.write_text("def fib(n) = n <= 1 ? n : fib(n - 1) + fib(n - 2)\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.ruby.shutil.which", lambda name: "ruby")
    monkeypatch.setattr(
        "anti_shortcut.languages.ruby.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=b"Syntax OK", stderr=b""),
    )
    ok, msg = RubyAdapter().check_syntax(f)
    assert ok and "ruby -c" in msg


def test_ruby_check_syntax_mock_error(tmp_path, monkeypatch):
    f = tmp_path / "broken.rb"
    f.write_text("def x(\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.ruby.shutil.which", lambda name: "ruby")
    monkeypatch.setattr(
        "anti_shortcut.languages.ruby.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout=b"", stderr=b"broken.rb:1: syntax error"
        ),
    )
    ok, msg = RubyAdapter().check_syntax(f)
    assert not ok and "Ruby" in msg and "syntax error" in msg


def test_rust_check_syntax_mock_cargo_ok(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir()
    f = src / "lib.rs"
    f.write_text("pub fn x() -> i64 { 1 }\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.rust.shutil.which", lambda name: "cargo")
    monkeypatch.setattr(
        "anti_shortcut.languages.rust.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )
    ok, msg = RustAdapter().check_syntax(f)
    assert ok and "cargo check" in msg


def test_rust_check_syntax_mock_cargo_error(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir()
    f = src / "lib.rs"
    f.write_text("pub fn x( -> i64 { 1 }\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.rust.shutil.which", lambda name: "cargo")
    monkeypatch.setattr(
        "anti_shortcut.languages.rust.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="error[E0767]: expected one of"
        ),
    )
    ok, msg = RustAdapter().check_syntax(f)
    assert not ok and "Rust 编译错误" in msg


def test_rust_check_syntax_mock_rustc_ok(tmp_path, monkeypatch):
    f = tmp_path / "standalone.rs"
    f.write_text("pub fn x() -> i64 { 1 }\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.rust.shutil.which", lambda name: "rustc")
    monkeypatch.setattr(
        "anti_shortcut.languages.rust.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )

    # rustc 单文件路径使用 tempfile；沙箱 / CI 中重定向到工作区，避免临时目录权限差异
    class _TmpDir:
        def __init__(self, base):
            self._d = base / "rustc_out"
            self._d.mkdir(exist_ok=True)

        def __enter__(self):
            return str(self._d)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "anti_shortcut.languages.rust.tempfile.TemporaryDirectory",
        lambda *a, **k: _TmpDir(tmp_path),
    )
    ok, msg = RustAdapter().check_syntax(f)
    assert ok and "rustc" in msg
