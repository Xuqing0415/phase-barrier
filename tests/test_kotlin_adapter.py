"""Kotlin 语言适配器测试（v0.32.0）：注册 / 检测 / 文件识别 / JUnit5 启发式 / 输出解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.languages import (
    LANGUAGE_REGISTRY,
    KotlinAdapter,
    detect_language,
    get_adapter,
)

KOTLIN_IMPL = """\
package fib

fun fib(n: Int): Int {
    require(n >= 0) { "n must be >= 0" }
    if (n <= 1) return n
    var a = 0
    var b = 1
    for (i in 2..n) {
        val tmp = a + b
        a = b
        b = tmp
    }
    return b
}
"""

KOTLIN_TESTS = """\
package fib

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class FibTest {
    @Test
    fun baseCases() {
        assertEquals(0, fib(0))
        assertEquals(1, fib(1))
    }

    @Test
    fun knownValue() {
        assertEquals(55, fib(10))
    }
}
"""

needs_kotlinc = pytest.mark.skipif(
    shutil.which("kotlinc") is None, reason="kotlinc 未安装"
)


# ---------- 注册与检测 ----------

def test_kotlin_adapter_registered():
    assert "kotlin" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["kotlin"] is KotlinAdapter


def test_kotlin_adapter_explicit_config(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("", encoding="utf-8")
    # build.gradle.kts 同时是 Java/Gradle 标志，自动检测为 java；显式指定 kotlin 生效
    assert detect_language(tmp_path) == "java"
    adapter = get_adapter(GateConfig(language="kotlin"), tmp_path)
    assert isinstance(adapter, KotlinAdapter)


def test_kotlin_adapter_detected_via_source_root(tmp_path):
    (tmp_path / "src" / "main" / "kotlin").mkdir(parents=True)
    (tmp_path / "src" / "main" / "kotlin" / "Fib.kt").write_text(
        "fun fib(n: Int): Int = n", encoding="utf-8"
    )
    assert detect_language(tmp_path) == "kotlin"
    assert isinstance(get_adapter(GateConfig(), tmp_path), KotlinAdapter)


# ---------- 文件识别 ----------

def test_kotlin_adapter_file_classification():
    a = KotlinAdapter()
    assert a.is_source_file(Path("src/main/kotlin/fib/Fib.kt"))
    assert a.is_source_file(Path("src/Calc.kt"))
    assert a.is_source_file(Path("lib/Calc.kt"))
    assert a.is_source_file(Path("Calc.kt"))
    assert not a.is_source_file(Path("src/test/kotlin/CalcTest.kt"))
    assert not a.is_source_file(Path("CalcTest.kt"))
    assert not a.is_source_file(Path("CalcTests.kt"))
    assert not a.is_source_file(Path("build.gradle.kts"))
    assert a.is_test_file(Path("CalcTest.kt"))
    assert a.is_test_file(Path("src/test/kotlin/CalcTest.kt"))
    assert a.is_test_file(Path("src/test/CalcTest.kt"))
    assert a.is_test_file(Path("test/kotlin/CalcTest.kt"))
    assert a.is_test_file(Path("spec/CalcTest.kt"))
    assert not a.is_test_file(Path("src/main/kotlin/Calc.kt"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_kotlin_adapter_analyze_junit5(tmp_path):
    f = tmp_path / "FibTest.kt"
    f.write_text(KOTLIN_TESTS, encoding="utf-8")
    info = KotlinAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 3


def test_kotlin_adapter_analyze_kotlin_test_asserts(tmp_path):
    f = tmp_path / "ListTest.kt"
    f.write_text(
        "import kotlin.test.Test\n"
        "import kotlin.test.assertEquals\n"
        "import kotlin.test.assertFailsWith\n"
        "class ListTest {\n"
        "    @Test\n"
        "    fun content() {\n"
        "        assertEquals(listOf(1), listOf(1))\n"
        "        assertFailsWith<IllegalArgumentException> { fib(-1) }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    info = KotlinAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] >= 2


def test_kotlin_adapter_analyze_empty(tmp_path):
    f = tmp_path / "EmptyTest.kt"
    f.write_text("class EmptyTest {}\n", encoding="utf-8")
    info = KotlinAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

@needs_kotlinc
def test_kotlin_adapter_check_syntax_with_kotlinc(tmp_path):
    f = tmp_path / "Fib.kt"
    f.write_text(KOTLIN_IMPL, encoding="utf-8")
    ok, msg = KotlinAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


@needs_kotlinc
def test_kotlin_adapter_check_syntax_reports_error(tmp_path):
    f = tmp_path / "broken.kt"
    f.write_text("fun fib( {\n", encoding="utf-8")
    ok, msg = KotlinAdapter().check_syntax(f)
    assert ok is False
    assert "Kotlin 语法错误" in msg


def test_kotlin_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "empty.kt"
    f.write_text("", encoding="utf-8")
    ok, msg = KotlinAdapter().check_syntax(f)
    assert ok is False and "空文件" in msg


def test_kotlin_adapter_check_syntax_missing_kotlinc(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    f = tmp_path / "x.kt"
    f.write_text("fun x() = 1\n", encoding="utf-8")
    ok, msg = KotlinAdapter().check_syntax(f)
    assert ok is False and "kotlinc" in msg


# ---------- 测试命令识别 ----------

def test_kotlin_adapter_identify_test_command():
    a = KotlinAdapter()
    assert a.identify_test_command("gradle test")
    assert a.identify_test_command("./gradlew test")
    assert a.identify_test_command("mvn test")
    assert a.identify_test_command("./mvnw test")
    assert a.identify_test_command("java -jar junit-platform-console-standalone.jar")
    assert not a.identify_test_command("python -m pytest")
    assert not a.identify_test_command("ls -la")


# ---------- 输出解析（复用 Java 适配器） ----------

def test_kotlin_parse_gradle_failed():
    out = "> Task :test FAILED\n3 tests completed, 1 failed\nBUILD FAILED\n"
    ok, summary = KotlinAdapter().parse_test_output(out, 1)
    assert ok is False and "1 failed" in summary


def test_kotlin_parse_gradle_passed():
    out = "3 tests completed\nBUILD SUCCESSFUL\n"
    ok, summary = KotlinAdapter().parse_test_output(out, 0)
    assert ok is True


def test_kotlin_parse_surefire_failed():
    out = (
        "Tests run: 3, Failures: 1, Errors: 0, Skipped: 0\n"
        "FibTest.baseCases <<< FAILURE!\n"
    )
    ok, summary = KotlinAdapter().parse_test_output(out, 1)
    assert ok is False and "Failures: 1" in summary


def test_kotlin_parse_empty_failed():
    ok, summary = KotlinAdapter().parse_test_output("", 2)
    assert ok is False and "退出码 2" in summary
