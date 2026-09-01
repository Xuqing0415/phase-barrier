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
