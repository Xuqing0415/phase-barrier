"""Rust 语言适配器测试：文件识别 / cargo check / rustc 语法检查 / cargo test 输出解析。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import LANGUAGE_REGISTRY, RustAdapter, detect_language, get_adapter
from anti_shortcut.validators import validate_tests
from conftest import SPEC, USER_REQUEST

CARGO_TOML = """\
[package]
name = "fib"
version = "0.1.0"
edition = "2021"
"""

RUST_IMPL = """\
pub fn fib(n: i64) -> i64 {
    if n <= 1 {
        return n;
    }
    let (mut a, mut b) = (0, 1);
    for _ in 2..=n {
        let next = a + b;
        a = b;
        b = next;
    }
    b
}
"""

RUST_TESTS = """\
use fib::fib;

#[test]
fn test_basic() {
    assert_eq!(fib(3), 2);
}

#[test]
fn test_negative() {
    assert_ne!(fib(-1), 999);
}
"""

needs_cargo = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo 未安装")
needs_rustc = pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc 未安装")


# ---------- 注册与检测 ----------

def test_rust_adapter_registered():
    assert "rust" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["rust"] is RustAdapter


def test_rust_adapter_detected_via_cargo_toml(tmp_path):
    (tmp_path / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
    assert detect_language(tmp_path) == "rust"
    cfg = GateConfig()
    assert isinstance(get_adapter(cfg, tmp_path), RustAdapter)


# ---------- 文件识别 ----------

def test_rust_adapter_file_classification():
    a = RustAdapter()
    assert a.is_test_file(Path("tests/fib_test.rs"))
    assert a.is_test_file(Path("tests/integration.rs"))
    assert a.is_test_file(Path("src/fib/tests.rs"))
    assert a.is_test_file(Path("src/lib_test.rs"))
    assert not a.is_test_file(Path("src/lib.rs"))
    assert a.is_source_file(Path("src/lib.rs"))
    assert a.is_source_file(Path("src/main.rs"))
    assert not a.is_source_file(Path("tests/fib_test.rs"))
    assert not a.is_source_file(Path("Cargo.toml"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_rust_adapter_analyze_tests(tmp_path):
    f = tmp_path / "fib_test.rs"
    f.write_text(RUST_TESTS, encoding="utf-8")
    info = RustAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_rust_adapter_analyze_tests_tokio_and_empty(tmp_path):
    a = RustAdapter()
    f = tmp_path / "async_test.rs"
    f.write_text("#[tokio::test]\nasync fn works() { assert!(true); }\n", encoding="utf-8")
    info = a.analyze_tests(f)
    assert len(info["test_functions"]) == 1
    e = tmp_path / "empty_test.rs"
    e.write_text("fn main() {}\n", encoding="utf-8")
    info2 = a.analyze_tests(e)
    assert info2["test_functions"] == []
    assert info2["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_rust_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.rs"
    f.write_text("", encoding="utf-8")
    ok, msg = RustAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_rust_adapter_check_syntax_missing_tools(tmp_path, monkeypatch):
    f = tmp_path / "lib.rs"
    f.write_text(RUST_IMPL, encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.rust.shutil.which", lambda name: None)
    ok, msg = RustAdapter().check_syntax(f)
    assert not ok and "Rust" in msg


@needs_cargo
def test_rust_adapter_check_syntax_cargo_ok(tmp_path):
    (tmp_path / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text(RUST_IMPL, encoding="utf-8")
    ok, msg = RustAdapter().check_syntax(src / "lib.rs")
    assert ok and "cargo check" in msg


@needs_cargo
def test_rust_adapter_check_syntax_cargo_error(tmp_path):
    (tmp_path / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn broken( -> i64 { 0 }\n", encoding="utf-8")
    ok, msg = RustAdapter().check_syntax(src / "lib.rs")
    assert not ok and ("Rust 编译错误" in msg or "Rust 语法错误" in msg)


@needs_rustc
def test_rust_adapter_check_syntax_rustc_single_ok(tmp_path):
    f = tmp_path / "standalone.rs"
    f.write_text("pub fn answer() -> i64 { 42 }\n", encoding="utf-8")
    ok, msg = RustAdapter().check_syntax(f)
    assert ok and "rustc" in msg


@needs_rustc
def test_rust_adapter_check_syntax_rustc_single_error(tmp_path):
    f = tmp_path / "broken.rs"
    f.write_text("pub fn broken( -> i64 { 0 }\n", encoding="utf-8")
    ok, msg = RustAdapter().check_syntax(f)
    assert not ok and "Rust 语法错误" in msg


# ---------- 测试命令识别 ----------

def test_rust_adapter_identify_test_command():
    a = RustAdapter()
    assert a.identify_test_command("cargo test")
    assert a.identify_test_command("cargo test -- --nocapture")
    assert a.identify_test_command("cargo nextest run")
    assert not a.identify_test_command("cargo build")
    assert not a.identify_test_command("cargo check")
    assert not a.identify_test_command("ls -la")


# ---------- 测试输出解析 ----------

def test_rust_adapter_parse_test_output():
    a = RustAdapter()
    ok, summary = a.parse_test_output("test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n", 0)
    assert ok and "test result: ok" in summary
    ok2, summary2 = a.parse_test_output("test result: FAILED. 1 passed; 1 failed\n", 1)
    assert not ok2 and "FAILED" in summary2
    ok3, summary3 = a.parse_test_output("error[E0308]: mismatched types", 1)
    assert not ok3
    ok4, _ = a.parse_test_output("running 2 tests", 0)
    assert ok4


# ---------- 校验器接线 ----------

def test_validate_tests_rust_with_language_config(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fib_test.rs").write_text(RUST_TESTS, encoding="utf-8")
    cfg = load_config({"language": "rust"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] == 2


# ---------- Skill 全流程验收 ----------

def test_skill_rust_full_flow(tmp_path, monkeypatch, fake_tools):
    """验收：language: rust 时阶段校验与工具拦截生效，可完整走通交付。"""
    monkeypatch.setattr(
        "anti_shortcut.languages.rust.shutil.which",
        lambda name: "cargo" if name == "cargo" else None,
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.rust.subprocess.run", fake_run)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")

    skill = AntiShortcutSkill(tmp_path, config={"language": "rust"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    assert isinstance(skill.adapter, RustAdapter)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("tests/fib_test.rs", RUST_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("src/lib.rs", RUST_IMPL)
    assert tools["advance_stage"](4)["success"]
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "summary": "test result: ok. 2 passed"})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete