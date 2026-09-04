"""Scala 语言适配器测试（v0.34.0）：注册 / 检测 / 文件识别 / scalac 语法 / 启发式统计。"""
import shutil

import pytest

from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import (
    LANGUAGE_REGISTRY,
    ScalaAdapter,
    detect_language,
    get_adapter,
)
from anti_shortcut.validators import validate_tests

SCALA_IMPL = '''\
object Fib {
    def fib(n: Int): Int = {
        require(n >= 0, "n must be >= 0")
        if (n <= 1) n else fib(n - 1) + fib(n - 2)
    }
}
'''

SCALA_FUNSUITE_TESTS = '''\
import org.scalatest.funsuite.AnyFunSuite

class FibTest extends AnyFunSuite {
    test("base cases") {
        assert(Fib.fib(0) == 0)
        assert(Fib.fib(1) == 1)
    }

    test("known value") {
        assert(Fib.fib(10) == 55)
    }
}
'''

SCALA_JUNIT_TESTS = '''\
import org.junit.Test
import org.junit.Assert.assertEquals

class FibTest {
    @Test
    def baseCases(): Unit = {
        assertEquals(0, Fib.fib(0))
        assertEquals(1, Fib.fib(1))
    }
}
'''

needs_scalac = pytest.mark.skipif(
    shutil.which("scalac") is None, reason="scalac 未安装"
)


# ---------- 注册与检测 ----------

def test_scala_adapter_registered():
    assert "scala" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["scala"] is ScalaAdapter


def test_scala_adapter_explicit_config(tmp_path):
    cfg = GateConfig(language="scala", workspace=tmp_path)
    assert isinstance(get_adapter(cfg, tmp_path), ScalaAdapter)


def test_scala_adapter_detected_via_build_sbt(tmp_path):
    (tmp_path / "build.sbt").write_text('name := "demo"\n', encoding="utf-8")
    assert detect_language(tmp_path) == "scala"


# ---------- 文件识别 ----------

def test_scala_adapter_file_classification():
    a = ScalaAdapter()
    assert a.is_test_file("src/test/scala/FibTest.scala")
    assert a.is_test_file("FibTest.scala")
    assert a.is_test_file("FibSpec.scala")
    assert a.is_test_file("src/test/scala/Helper.scala")
    assert not a.is_test_file("src/main/scala/Fib.scala")
    assert a.is_source_file("src/main/scala/Fib.scala")
    assert a.is_source_file("Fib.scala")
    assert not a.is_source_file("FibTest.scala")
    assert not a.is_source_file("README.md")


# ---------- 测试统计（启发式） ----------

def test_scala_adapter_analyze_funsuite(tmp_path):
    f = tmp_path / "FibTest.scala"
    f.write_text(SCALA_FUNSUITE_TESTS, encoding="utf-8")
    info = ScalaAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_scala_adapter_analyze_junit(tmp_path):
    f = tmp_path / "FibTest.scala"
    f.write_text(SCALA_JUNIT_TESTS, encoding="utf-8")
    info = ScalaAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] >= 2


def test_scala_adapter_analyze_empty(tmp_path):
    f = tmp_path / "EmptyTest.scala"
    f.write_text("package demo\n", encoding="utf-8")
    info = ScalaAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_scala_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.scala"
    f.write_text("", encoding="utf-8")
    ok, msg = ScalaAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_scala_adapter_check_syntax_missing_scalac(tmp_path, monkeypatch):
    f = tmp_path / "Fib.scala"
    f.write_text(SCALA_IMPL, encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.scala.shutil.which", lambda name: None)
    ok, msg = ScalaAdapter().check_syntax(f)
    assert not ok and "scalac" in msg


def test_scala_adapter_check_syntax_ok(tmp_path, monkeypatch):
    f = tmp_path / "Fib.scala"
    f.write_text(SCALA_IMPL, encoding="utf-8")

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("anti_shortcut.languages.scala.shutil.which", lambda name: "scalac")
    monkeypatch.setattr("anti_shortcut.languages.scala.subprocess.run", lambda *a, **k: FakeProc())
    ok, msg = ScalaAdapter().check_syntax(f)
    assert ok and "scalac" in msg


def test_scala_adapter_check_syntax_dependency_degrade(tmp_path, monkeypatch):
    f = tmp_path / "Fib.scala"
    f.write_text(SCALA_IMPL, encoding="utf-8")

    class FakeProc:
        returncode = 1
        stderr = "Fib.scala:5: error: not found: value helper\n"
        stdout = ""

    monkeypatch.setattr("anti_shortcut.languages.scala.shutil.which", lambda name: "scalac")
    monkeypatch.setattr("anti_shortcut.languages.scala.subprocess.run", lambda *a, **k: FakeProc())
    ok, msg = ScalaAdapter().check_syntax(f)
    assert ok and "需在完整项目中编译验证" in msg


def test_scala_adapter_check_syntax_error(tmp_path, monkeypatch):
    f = tmp_path / "Broken.scala"
    f.write_text("object Fib {\n  def fib(n: Int): Int = {\n", encoding="utf-8")

    class FakeProc:
        returncode = 1
        stderr = "Broken.scala:2: error: '}' expected but end of file found.\n"
        stdout = ""

    monkeypatch.setattr("anti_shortcut.languages.scala.shutil.which", lambda name: "scalac")
    monkeypatch.setattr("anti_shortcut.languages.scala.subprocess.run", lambda *a, **k: FakeProc())
    ok, msg = ScalaAdapter().check_syntax(f)
    assert not ok and "Scala 语法错误" in msg


# ---------- 真实工具链（CI 已安装 scalac） ----------

@needs_scalac
def test_scala_adapter_check_syntax_real_scalac_ok(tmp_path):
    f = tmp_path / "Fib.scala"
    f.write_text(SCALA_IMPL, encoding="utf-8")
    ok, msg = ScalaAdapter().check_syntax(f)
    assert ok, msg


@needs_scalac
def test_scala_adapter_check_syntax_real_scalac_error(tmp_path):
    f = tmp_path / "Broken.scala"
    f.write_text("object Fib {\n  def fib(n: Int): Int = {\n", encoding="utf-8")
    ok, msg = ScalaAdapter().check_syntax(f)
    assert not ok and "Scala 语法错误" in msg


# ---------- 测试命令识别 ----------

def test_scala_adapter_identify_test_command():
    a = ScalaAdapter()
    assert a.identify_test_command("sbt test")
    assert a.identify_test_command("./sbt test")
    assert a.identify_test_command("scala-cli test")
    assert a.identify_test_command("mvn test")
    assert a.identify_test_command("gradle test")
    assert not a.identify_test_command("ls -la")
    assert not a.identify_test_command("sbt compile")


# ---------- 测试输出解析 ----------

def test_scala_adapter_parse_scalatest_pass():
    a = ScalaAdapter()
    text = (
        "Run completed in 618 milliseconds.\n"
        "Total number of tests run: 3\n"
        "Suites: completed 2, aborted 0\n"
        "Tests: succeeded 3, failed 0, canceled 0, ignored 0, pending 0\n"
        "All tests passed.\n"
    )
    ok, summary = a.parse_test_output(text, 0)
    assert ok and "ScalaTest" in summary and "succeeded 3" in summary


def test_scala_adapter_parse_scalatest_fail():
    a = ScalaAdapter()
    text = (
        "Total number of tests run: 3\n"
        "Tests: succeeded 2, failed 1, canceled 0, ignored 0, pending 0\n"
        "*** 1 TEST FAILED ***\n"
    )
    ok, summary = a.parse_test_output(text, 1)
    assert not ok and "failed 1" in summary


def test_scala_adapter_parse_falls_back_to_java():
    a = ScalaAdapter()
    ok, summary = a.parse_test_output("Tests run: 3, Failures: 1, Errors: 0, Skipped: 0", 1)
    assert not ok and "Failures: 1" in summary


# ---------- 校验器接线 ----------

def test_validate_tests_scala_with_language_config(tmp_path):
    p = tmp_path / "src" / "test" / "scala"
    p.mkdir(parents=True)
    (p / "FibTest.scala").write_text(SCALA_FUNSUITE_TESTS, encoding="utf-8")
    cfg = load_config({"language": "scala"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok, msg