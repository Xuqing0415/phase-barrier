"""C/C++ 语言适配器（v0.26.0 起，v0.28.0 增强）：编译器语法检查 + GoogleTest/Catch2 启发式。

- 文件识别：``*.cpp`` / ``*.cc`` / ``*.cxx`` / ``*.c`` / ``*.h`` / ``*.hpp`` 为实现；
  测试文件为 ``test_*`` / ``*_test`` 前缀/后缀（或位于 ``tests/`` 目录）
- 语法检查：C++ 优先 ``g++ -fsyntax-only -x c++``，回退 ``clang++``；
  C 文件优先 ``gcc -fsyntax-only -x c``，回退 ``clang`` / ``cc``；
  工具缺失时返回明确错误（不静默放行）
- 测试统计：GoogleTest 宏 ``TEST(`` / ``TEST_F(`` / ``TEST_P(`` / ``TEST_T(`` 与
  Catch2 宏 ``TEST_CASE(`` / ``SCENARIO(`` / ``TEST_CASE_METHOD(`` 数量
  + ``EXPECT_*`` / ``ASSERT_*`` / ``REQUIRE*`` / ``CHECK*`` 断言关键字（启发式）
- 测试命令：``ctest`` / ``cmake --build ... --target test`` / ``make test`` / ``./run_tests``
- 输出解析：GoogleTest ``[  PASSED  ] N tests`` / ``[  FAILED  ] M tests`` /
  ``[ RUN      ] Suite.Test``，ctest ``100% tests passed``，
  Catch2 ``All tests passed (N assertions in M test cases)`` / ``FAILED:``

语法检查依赖外部工具（GCC / Clang）；缺失时校验失败并提示安装。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["CppAdapter"]

# GoogleTest 测试声明：TEST / TEST_F / TEST_P / TEST_T / TEST_F_P（忽略参数化宏的模板括号）
_CPP_TEST_MACRO_RE = re.compile(
    r"^\s*TEST(?:_[FPT])?\s*\(\s*[\w:]+\s*,\s*[\w:]+\s*\)",
    re.M,
)
# Catch2 测试声明：TEST_CASE / SCENARIO / TEST_CASE_METHOD / TEMPLATE_TEST_CASE 等
_CPP_CATCH2_MACRO_RE = re.compile(
    r"^\s*(?:TEMPLATE_(?:PRODUCT_)?)?(?:TEST_CASE|SCENARIO)(?:_METHOD)?\s*\(",
    re.M,
)
# 断言关键字：GoogleTest EXPECT_* / ASSERT_*；Catch2 REQUIRE* / CHECK*（含 _FALSE / _THROWS 等变体）
_CPP_ASSERT_RE = re.compile(
    r"\b(?:EXPECT|ASSERT)_[A-Z_][A-Z0-9_]*\s*\(|"
    r"\b(?:REQUIRE|CHECK)(?:_FALSE|_THROWS(?:_AS|_WITH)?|_NOTHROW|_MATCHES)?\s*\(",
    re.M,
)

# GoogleTest 输出：``[ RUN      ] Suite.Test`` / ``[       OK ] Suite.Test`` /
# ``[  FAILED  ] Suite.Test (5 ms)`` / ``[  PASSED  ] 3 tests.`` / ``[  FAILED  ] 1 test.``
_GTEST_RUN_RE = re.compile(r"\[\s*RUN\s*\]\s+(\S+)", re.M)
_GTEST_FAIL_TEST_RE = re.compile(r"\[\s*FAILED\s*\]\s+[\w:.]+(?:\s+\()", re.M)
_GTEST_PASSED_COUNT_RE = re.compile(r"\[\s*PASSED\s*\]\s+(\d+)\s+tests?", re.M)
_GTEST_FAILED_COUNT_RE = re.compile(r"\[\s*FAILED\s*\]\s+(\d+)\s+tests?", re.M)
_GTEST_FAIL_HEADER_RE = re.compile(r"\[\s*FAILED\s*\]", re.M)
_CTEST_OK_RE = re.compile(r"^\s*100%\s+tests passed", re.M)
_CTEST_FAIL_RE = re.compile(r"tests\s+(failed|passed)", re.IGNORECASE)
# Catch2 输出：``All tests passed (10 assertions in 5 test cases)`` /
# ``test cases: 5 | 2 passed | 3 failed`` / ``FAILED:``
_CATCH2_PASS_RE = re.compile(
    r"All tests passed\s*\((\d+)\s+assertions?\s+in\s+(\d+)\s+test\s+cases?\)",
    re.M,
)
_CATCH2_CASES_RE = re.compile(
    r"test\s+cases?:\s+(?P<total>\d+)\s+\|\s+(?P<passed>\d+)\s+passed"
    r"(?:\s*\|\s*(?P<failed>\d+)\s+failed)?",
    re.M,
)
_CATCH2_FAILED_RE = re.compile(r"^\s*FAILED:", re.M)


def _decode_output(raw: bytes | None) -> str:
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


class CppAdapter(LanguageAdapter):
    """C/C++：g++/gcc 语法检查 + GoogleTest / Catch2 宏启发式测试统计。"""

    name = "cpp"
    file_extensions = [".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"]
    source_file_patterns = [
        "*.cpp", "*.cc", "*.cxx", "*.c", "*.h", "*.hpp",
        "**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.c",
    ]
    test_file_patterns = [
        "test_*.cpp", "*_test.cpp", "test_*.cc", "*_test.cc",
        "test_*.c", "*_test.c",
        "**/test_*.cpp", "**/*_test.cpp", "**/tests/**/*.cpp", "**/test/**/*.cpp",
        "**/test_*.c", "**/*_test.c", "**/tests/**/*.c", "**/test/**/*.c",
    ]
    test_command_patterns = [
        r"^\s*ctest\b",
        r"^\s*cmake\b.*(--build\b.*--target\s+test|--build\b.*-t\s+test)",
        r"^\s*make\s+test\b",
        r"^\s*\.?/?run_tests\b",
        r"^\s*\.?/?build/tests?\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        is_c = path.suffix.lower() == ".c"
        label = "C" if is_c else "C++"
        compiler, lang_flag, std = self._find_compiler(is_c)
        if compiler is None:
            names = "gcc / clang" if is_c else "g++ / clang++"
            return False, (
                f"未检测到 {label} 编译器（{names}），无法对 {path.name} 做语法检查；"
                "请安装 GCC（https://gcc.gnu.org/）或 Clang（https://clang.llvm.org/），"
                "或在配置中改用其他语言适配器"
            )
        proc = subprocess.run(
            [compiler, "-fsyntax-only", std, "-x", lang_flag, str(path)],
            capture_output=True,
        )
        if proc.returncode == 0:
            return True, f"语法检查通过（{os.path.basename(compiler)} -fsyntax-only）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
        first = lines[0] if lines else stderr.strip()
        return False, f"{label} 语法错误: {first[:500]}"

    @staticmethod
    def _find_compiler(is_c: bool = False) -> tuple[str | None, str, str]:
        """按语言选择编译器，返回 (编译器路径, -x 语言标志, -std 参数)；缺失返回 None。"""
        if is_c:
            for name in ("gcc", "clang", "cc"):
                found = shutil.which(name)
                if found:
                    return found, "c", "-std=c17"
            return None, "c", "-std=c17"
        for name in ("g++", "clang++", "c++"):
            found = shutil.which(name)
            if found:
                return found, "c++", "-std=c++17"
        return None, "c++", "-std=c++17"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        macros = _CPP_TEST_MACRO_RE.findall(text) + _CPP_CATCH2_MACRO_RE.findall(text)
        assertions_total = len(_CPP_ASSERT_RE.findall(text))
        tests = [
            {"name": f"<{i + 1}:{m.strip()}>", "assertions": 0, "heuristic": True}
            for i, m in enumerate(macros)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（GoogleTest / Catch2 / ctest） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        text = output or ""
        if exit_code == 0:
            m = _GTEST_PASSED_COUNT_RE.search(text)
            if m:
                return True, f"GoogleTest 通过：{m.group(1)} 个测试全部通过"
            m = _CATCH2_PASS_RE.search(text)
            if m:
                return True, (
                    f"Catch2 通过：{m.group(2)} 个测试用例 / "
                    f"{m.group(1)} 条断言全部通过"
                )
            if _CTEST_OK_RE.search(text):
                return True, "ctest 通过：100% tests passed"
            ran = _GTEST_RUN_RE.findall(text)
            if ran:
                return True, f"测试通过（GoogleTest，{len(ran)} 个用例运行）"
            return True, "所有测试通过（ctest / GoogleTest / Catch2）"
        failed = _GTEST_FAIL_TEST_RE.findall(text)
        if failed:
            names = "、".join(dict.fromkeys(failed))[:400]
            suffix = f"（共 {len(failed)} 个）" if len(failed) > 1 else ""
            return False, f"GoogleTest 存在失败用例: {names}{suffix}"
        m = _GTEST_FAILED_COUNT_RE.search(text)
        if m:
            return False, f"GoogleTest 失败：{m.group(1)} 个测试未通过"
        if _GTEST_FAIL_HEADER_RE.search(text):
            return False, "GoogleTest 存在失败用例（[  FAILED  ]）"
        m = _CATCH2_CASES_RE.search(text)
        if m and int(m.group("failed") or 0) > 0:
            return False, (
                f"Catch2 失败：{m.group('total')} 个测试用例，"
                f"{m.group('failed')} 个未通过"
            )
        if _CATCH2_FAILED_RE.search(text):
            return False, "Catch2 存在失败用例（FAILED:）"
        if _CTEST_FAIL_RE.search(text):
            return False, "ctest 存在失败或未通过的测试"
        return False, f"测试失败，退出码 {exit_code}"
