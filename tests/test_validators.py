"""证据校验器模块测试。"""
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.state import StateManager
from anti_shortcut.validators import (
    analyze_test_file,
    classify_path,
    path_matches,
    validate_implementation,
    validate_retest,
    validate_spec,
    validate_test_run,
    validate_tests,
)
from conftest import BUGGY_IMPL, EMPTY_TESTS, GOOD_IMPL, GOOD_TESTS, SPEC


@pytest.fixture
def cfg():
    return GateConfig()


def make_state(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "state.json", user_request="r")


# ---------- path_matches / classify_path ----------

def test_path_matches_glob(tmp_path):
    assert path_matches(Path("test_x.py"), ["test_*.py"])
    assert path_matches(Path("tests/test_x.py"), ["tests/**/test_*.py"])
    assert path_matches(Path("tests/a/b/test_x.py"), ["tests/**/test_*.py"])
    assert not path_matches(Path("src/test_x.py"), ["tests/**/test_*.py"])
    assert path_matches(Path("foo_test.py"), ["*_test.py"])
    # \u5e26\u76ee\u5f55\u7684\u5b8c\u6574\u8def\u5f84\u4e5f\u53ef\u547d\u4e2d\u6587\u4ef6\u540d\u6a21\u5f0f
    assert path_matches(Path("src/fib.py"), ["*.py"])


def test_classify_path(cfg):
    assert classify_path("fib.py", cfg) == "source"
    assert classify_path("tests/test_fib.py", cfg) == "test"
    assert classify_path("spec.md", cfg) == "other"


# ---------- analyze_test_file ----------

def test_analyze_test_file_counts_asserts(tmp_path):
    p = tmp_path / "test_x.py"
    p.write_text(GOOD_TESTS, encoding="utf-8")
    info = analyze_test_file(p)
    assert info is not None
    names = [t["name"] for t in info["test_functions"]]
    assert names == ["test_base_cases", "test_known_value", "test_rejects_negative"]
    assert all(t["assertions"] >= 1 for t in info["test_functions"])


def test_analyze_test_file_syntax_error(tmp_path):
    p = tmp_path / "test_bad.py"
    p.write_text("def test_x(:\n    pass\n", encoding="utf-8")
    assert analyze_test_file(p) is None


# ---------- validate_spec ----------

def test_spec_missing_file(tmp_path, cfg):
    ok, msg, _ = validate_spec(tmp_path, cfg, None)
    assert not ok and "spec.md" in msg


def test_spec_missing_section(tmp_path, cfg):
    (tmp_path / "spec.md").write_text("# x\n## \u9700\u6c42\u5206\u6790\n...\n", encoding="utf-8")
    ok, msg, _ = validate_spec(tmp_path, cfg, None)
    assert not ok and "\u63a5\u53e3\u5b9a\u4e49" in msg


def test_spec_too_short(tmp_path, cfg):
    (tmp_path / "spec.md").write_text("## \u9700\u6c42\u5206\u6790\n## \u8bbe\u8ba1\u65b9\u6848\n## \u63a5\u53e3\u5b9a\u4e49\n", encoding="utf-8")
    ok, msg, _ = validate_spec(tmp_path, cfg, None)
    assert not ok and "\u8fc7\u4e8e\u7b80\u7565" in msg


def test_spec_pass(tmp_path, cfg):
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    ok, msg, ev = validate_spec(tmp_path, cfg, None)
    assert ok
    assert ev["sha256"] and ev["chars"] >= cfg.spec_min_chars


# ---------- validate_tests ----------

def test_tests_no_files(tmp_path, cfg):
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok


def test_tests_empty_shell_rejected(tmp_path, cfg):
    (tmp_path / "test_empty.py").write_text(EMPTY_TESTS, encoding="utf-8")
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok  # \u6d4b\u8bd5\u51fd\u6570\u6570\u91cf\u4e0d\u8db3 1 < 2


def test_tests_syntax_error_rejected(tmp_path, cfg):
    (tmp_path / "test_bad.py").write_text("def test_x(:\n", encoding="utf-8")
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok and "\u8bed\u6cd5\u9519\u8bef" in msg


def test_tests_no_assert_rejected(tmp_path, cfg):
    (tmp_path / "test_noassert.py").write_text(
        "def test_a():\n    pass\n\ndef test_b():\n    pass\n", encoding="utf-8"
    )
    ok, msg, _ = validate_tests(tmp_path, cfg, None)
    assert not ok and "\u7a7a\u58f3" in msg


def test_tests_pass(tmp_path, cfg):
    (tmp_path / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] == 3
    assert "test_fib.py" in ev["sha256"]


# ---------- validate_implementation ----------

def test_impl_missing(tmp_path, cfg):
    (tmp_path / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    ok, msg, _ = validate_implementation(tmp_path, cfg, None)
    assert not ok


def test_impl_syntax_error(tmp_path, cfg):
    (tmp_path / "fib.py").write_text("def fib(:\n", encoding="utf-8")
    ok, msg, _ = validate_implementation(tmp_path, cfg, None)
    assert not ok and "\u8bed\u6cd5\u9519\u8bef" in msg


def test_impl_pass(tmp_path, cfg):
    (tmp_path / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
    ok, msg, ev = validate_implementation(tmp_path, cfg, None)
    assert ok
    assert "fib.py" in ev["sha256"]


# ---------- validate_test_run / validate_retest ----------

def test_test_run_no_record(tmp_path, cfg):
    s = make_state(tmp_path)
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert not ok


def test_test_run_with_failed_record(tmp_path, cfg):
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 1, "passed": False, "summary": "1 failed"})
    ok, msg, _ = validate_test_run(tmp_path, cfg, s)
    assert ok  # \u8bb0\u5f55\u5b58\u5728\u5373\u901a\u8fc7\uff08\u5206\u652f\u5728 advance \u4e2d\u5904\u7406\uff09


def test_retest_no_record(tmp_path, cfg):
    s = make_state(tmp_path)
    ok, msg, _ = validate_retest(tmp_path, cfg, s)
    assert not ok


def test_retest_failed_record(tmp_path, cfg):
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 1, "passed": False})
    ok, msg, _ = validate_retest(tmp_path, cfg, s)
    assert not ok and "\u672a\u901a\u8fc7" in msg


def test_retest_passed_but_changed_after(tmp_path, cfg):
    s = make_state(tmp_path)
    s.mark_test_run({"exit_code": 0, "passed": True})
    s.mark_source_change("fib.py")  # \u6d4b\u8bd5\u540e\u4fee\u6539
    ok, msg, _ = validate_retest(tmp_path, cfg, s)
    assert not ok and "\u91cd\u65b0\u8fd0\u884c\u6d4b\u8bd5" in msg


def test_retest_passed_after_change(tmp_path, cfg):
    s = make_state(tmp_path)
    s.mark_source_change("fib.py")
    s.mark_test_run({"exit_code": 0, "passed": True})
    ok, msg, ev = validate_retest(tmp_path, cfg, s)
    assert ok
    assert ev["after_last_change"] is True
