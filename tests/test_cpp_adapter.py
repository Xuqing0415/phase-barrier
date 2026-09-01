"""C++ 语言适配器测试（v0.26.0）：注册 / 检测 / 文件识别 / GoogleTest 启发式 / 输出解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.languages import CppAdapter, LANGUAGE_REGISTRY, detect_language, get_adapter

CPP_IMPL = """\
#include <cstdint>

int64_t fib(int n) {
    if (n < 0) return -1;
    return n < 2 ? n : fib(n - 1) + fib(n - 2);
}
"""

CPP_TESTS = """\
#include <gtest/gtest.h>

int fib(int n);

TEST(FibTest, Base) {
    EXPECT_EQ(0, fib(0));
    EXPECT_EQ(1, fib(1));
}

TEST_F(FibTest, Sequence) {
    EXPECT_EQ(55, fib(10));
}
"""


# ---------- 注册与检测 ----------

def test_cpp_adapter_registered():
    assert "cpp" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["cpp"] is CppAdapter


def test_cpp_adapter_detected_via_cmake(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    assert detect_language(tmp_path) == "cpp"


def test_cpp_adapter_detected_via_vcxproj(tmp_path):
    (tmp_path / "App.vcxproj").write_text("<Project />", encoding="utf-8")
    assert detect_language(tmp_path) == "cpp"
    assert isinstance(get_adapter(GateConfig(), tmp_path), CppAdapter)


# ---------- 文件识别 ----------

def test_cpp_adapter_file_classification():
    a = CppAdapter()
    assert a.is_source_file(Path("src/main.cpp"))
    assert a.is_source_file(Path("include/fib.hpp"))
    assert a.is_source_file(Path("fib.cc"))
    assert not a.is_source_file(Path("test_fib.cpp"))
    assert a.is_test_file(Path("test_fib.cpp"))
    assert a.is_test_file(Path("fib_test.cc"))
    assert a.is_test_file(Path("tests/fib_test.cpp"))
    assert not a.is_test_file(Path("src/main.cpp"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_cpp_adapter_analyze_gtest(tmp_path):
    f = tmp_path / "test_fib.cpp"
    f.write_text(CPP_TESTS, encoding="utf-8")
    info = CppAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 3


def test_cpp_adapter_analyze_empty(tmp_path):
    f = tmp_path / "test_fib.cpp"
    f.write_text("#include <gtest/gtest.h>\n", encoding="utf-8")
    info = CppAdapter().analyze_tests(f)
    assert info["test_functions"] == []


# ---------- 语法检查 ----------

def test_cpp_adapter_check_syntax_with_compiler(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("无 g++ / clang++，跳过真实编译")
    f = tmp_path / "fib.cpp"
    f.write_text(CPP_IMPL, encoding="utf-8")
    ok, msg = CppAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


def test_cpp_adapter_check_syntax_reports_error(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("无 g++ / clang++，跳过真实编译")
    f = tmp_path / "broken.cpp"
    f.write_text("int main( { return 0; }\n", encoding="utf-8")
    ok, msg = CppAdapter().check_syntax(f)
    assert ok is False
    assert "C++ 语法错误" in msg


# ---------- 输出解析 ----------

def test_cpp_parse_passed_gtest():
    out = """[==========] Running 2 tests from 1 test suite.\n[  PASSED  ] 2 tests.\n"""
    ok, summary = CppAdapter().parse_test_output(out, 0)
    assert ok is True and "2" in summary


def test_cpp_parse_failed_gtest_lists_cases():
    out = """[ RUN      ] FibTest.Base\n[  FAILED  ] FibTest.Base (2 ms)\n[  FAILED  ] 1 test, listed below:\n[  FAILED  ] FibTest.Base\n"""
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False
    assert "FibTest" in summary


def test_cpp_parse_failed_count():
    out = """[  FAILED  ] 3 tests, listed below:\n"""
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False and "3" in summary


def test_cpp_parse_ctest_passed():
    out = "100% tests passed, 0 tests failed out of 4\n"
    ok, summary = CppAdapter().parse_test_output(out, 0)
    assert ok is True


def test_cpp_parse_unknown_failure():
    ok, summary = CppAdapter().parse_test_output("", 7)
    assert ok is False and "7" in summary


# ---------- 输出解析 / 工具缺失边界（v0.26.0 覆盖率门禁） ----------

def test_cpp_parse_passed_run_lines_without_count():
    # exit 0 且有 [ RUN ] 行但无 PASSED/FAILED 计数：按运行用例数判定通过
    out = "[ RUN      ] FibTest.Base\n[ RUN      ] FibTest.Next\n"
    ok, summary = CppAdapter().parse_test_output(out, 0)
    assert ok is True and "2" in summary


def test_cpp_parse_passed_fallback():
    # exit 0 且无任何 GoogleTest / ctest 标记：兜底判定通过
    ok, summary = CppAdapter().parse_test_output("build finished", 0)
    assert ok is True and "所有测试通过" in summary


def test_cpp_parse_failed_header():
    # exit != 0 且出现 [  FAILED  ] 头：判定存在失败用例
    out = "[  FAILED  ] FibTest.Base\n"
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False and "失败用例" in summary


def test_cpp_parse_ctest_failed():
    # exit != 0 且输出含 "tests failed"：判定 ctest 失败
    out = "50% tests passed, 2 tests failed out of 4\n"
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False and "ctest" in summary


def test_cpp_decode_output_none():
    from anti_shortcut.languages.cpp import _decode_output

    assert _decode_output(None) == ""


def test_cpp_decode_output_fallback_latin1():
    # utf-8 / gbk / cp1252 均无法解码时回退 latin-1
    from anti_shortcut.languages.cpp import _decode_output

    assert _decode_output(b"\x81") == "\x81"


def test_cpp_check_syntax_empty_file(tmp_path):
    f = tmp_path / "empty.cpp"
    f.write_text("", encoding="utf-8")
    ok, msg = CppAdapter().check_syntax(f)
    assert ok is False and "空文件" in msg


def test_cpp_check_syntax_missing_compiler(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    f = tmp_path / "x.cpp"
    f.write_text("int main() { return 0; }\n", encoding="utf-8")
    ok, msg = CppAdapter().check_syntax(f)
    assert ok is False and "编译器" in msg
# ---------- v0.28.0：C 文件与 Catch2 增强 ----------

C_TESTS = """\
#include <assert.h>

int fib(int n);

TEST_CASE("fib base", "[fib]") {
    REQUIRE(fib(0) == 0);
    CHECK(fib(1) == 1);
}

SCENARIO("fib sequence") {
    GIVEN("n = 10") {
        THEN("result is 55") {
            REQUIRE(fib(10) == 55);
        }
    }
}
"""


def test_cpp_adapter_c_file_classification():
    a = CppAdapter()
    assert a.is_source_file(Path("src/main.c"))
    assert a.is_source_file(Path("fib.c"))
    assert a.is_test_file(Path("test_fib.c"))
    assert a.is_test_file(Path("fib_test.c"))
    assert a.is_test_file(Path("tests/fib_test.c"))
    assert not a.is_test_file(Path("src/main.c"))
    assert not a.is_source_file(Path("test_fib.c"))


def test_cpp_adapter_analyze_catch2(tmp_path):
    f = tmp_path / "test_fib.cpp"
    f.write_text(C_TESTS, encoding="utf-8")
    info = CppAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 3  # REQUIRE x2 + CHECK x1


def test_cpp_adapter_parse_passed_catch2():
    out = "All tests passed (10 assertions in 5 test cases)\n"
    ok, summary = CppAdapter().parse_test_output(out, 0)
    assert ok is True and "Catch2" in summary and "5" in summary


def test_cpp_adapter_parse_failed_catch2_cases():
    out = "test cases: 5 | 2 passed | 3 failed\nassertions: 10 | 7 passed | 3 failed\n"
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False and "Catch2 失败" in summary and "3" in summary


def test_cpp_adapter_parse_failed_catch2_header():
    out = "FAILED:\n  test_add\n"
    ok, summary = CppAdapter().parse_test_output(out, 1)
    assert ok is False and "FAILED:" in summary


def test_cpp_adapter_check_syntax_c_file(tmp_path):
    compiler = shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
    if compiler is None:
        pytest.skip("无 gcc / clang / cc，跳过 C 语法检查")
    f = tmp_path / "fib.c"
    f.write_text("int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }\n", encoding="utf-8")
    ok, msg = CppAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg
