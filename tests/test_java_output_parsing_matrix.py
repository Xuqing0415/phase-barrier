"""Java 适配器输出解析框架矩阵回归测试（v0.44.0）。

用 tests/fixtures/java_outputs/ 下的真实风格样例覆盖：
Maven Surefire（JUnit5） / TestNG（Maven + Gradle） / Gradle JUnit5
（含括号与参数化行） / JUnit Platform Console，
对版本差异（Skips vs Skipped、Errors 并入失败）做边界锁定。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from anti_shortcut.languages import JavaAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "java_outputs"

# (fixture 文件名, exit_code, 期望 ok, 摘要中必须出现的片段)
CASES = [
    ("surefire-junit5-pass.txt", 0, True, ["Tests run: 9"]),
    ("surefire-junit5-errors-fail.txt", 1, False, ["Errors: 2", "addNegative(com.example.CalcTest)"]),
    ("testng-maven-pass.txt", 0, True, ["Total tests run: 4", "Skips: 1"]),
    ("testng-maven-fail.txt", 1, False, ["testLogin(com.example.AuthTest)"]),
    ("gradle-junit5-pass.txt", 0, True, ["4 tests completed", "0 failed"]),
    ("gradle-junit5-fail.txt", 1, False, ["CalcTest > [1] add(int, int)", "2 failed", "addBasic()"]),
    ("junit-console-pass.txt", 0, True, ["tests successful"]),
    ("junit-console-fail.txt", 1, False, ["tests failed", "addBasic"]),
    ("gradle-testng-fail.txt", 1, False, ["Total tests run: 3", "verifyResult"]),
    ("surefire-testng-skipped-variant.txt", 1, False, ["Skipped: 2", "testTimeout(com.example.CalcTest)"]),
]


@pytest.mark.parametrize(
    "name,exit_code,ok_expected,fragments",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_java_output_parsing_matrix(name: str, exit_code: int, ok_expected: bool, fragments: list[str]):
    adapter = JavaAdapter()
    output = (FIXTURES / name).read_text(encoding="utf-8")
    ok, summary = adapter.parse_test_output(output, exit_code)
    assert ok is ok_expected, f"{name}: ok={ok} summary={summary!r}"
    for frag in fragments:
        assert frag in summary, f"{name}: summary 缺少 {frag!r}: {summary!r}"


def test_gradle_parameterized_and_paren_failure_names():
    """Gradle JUnit5 失败行包含方法括号与参数化索引时能提取名称。"""
    adapter = JavaAdapter()
    out = (
        "com.example.CalcTest > [1] add(int, int) FAILED\n"
        "    org.opentest4j.AssertionFailedError at CalcTest.java:42\n"
        "com.example.CalcTest > [2] add(int, int) SKIPPED\n"
        "3 tests completed, 1 failed\n"
    )
    ok, summary = adapter.parse_test_output(out, 1)
    assert ok is False
    assert "CalcTest > [1] add(int, int)" in summary


def test_testng_gradle_run_failure_names_merged():
    adapter = JavaAdapter()
    out = (
        "com.example.CalcTest > verifyResult FAILED\n"
        "Total tests run: 3, Failures: 1, Skips: 0\n"
    )
    ok, summary = adapter.parse_test_output(out, 1)
    assert ok is False
    assert "verifyResult" in summary
