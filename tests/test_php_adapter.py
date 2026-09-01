"""PHP 语言适配器测试（v0.28.0）：注册 / 检测 / 文件识别 / PHPUnit 启发式 / 输出解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.languages import LANGUAGE_REGISTRY, PhpAdapter, detect_language, get_adapter

PHP_IMPL = """\
<?php

function fib(int $n): int {
    if ($n < 0) {
        throw new InvalidArgumentException("negative");
    }
    return $n < 2 ? $n : fib($n - 1) + fib($n - 2);
}
"""

PHP_TESTS = """\
<?php

use PHPUnit\\Framework\\TestCase;

final class FibTest extends TestCase
{
    public function testBase(): void
    {
        $this->assertSame(0, fib(0));
        $this->assertSame(1, fib(1));
    }

    public function testSequence(): void
    {
        $this->assertSame(55, fib(10));
    }
}
"""

needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="PHP CLI 未安装")


# ---------- 注册与检测 ----------

def test_php_adapter_registered():
    assert "php" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["php"] is PhpAdapter


def test_php_adapter_detected_via_composer(tmp_path):
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    assert detect_language(tmp_path) == "php"
    assert isinstance(get_adapter(GateConfig(), tmp_path), PhpAdapter)


# ---------- 文件识别 ----------

def test_php_adapter_file_classification():
    a = PhpAdapter()
    assert a.is_source_file(Path("index.php"))
    assert a.is_source_file(Path("src/Calc.php"))
    assert a.is_source_file(Path("app/Services/Calc.php"))
    assert not a.is_source_file(Path("CalcTest.php"))
    assert not a.is_source_file(Path("tests/Helper.php"))
    assert a.is_test_file(Path("CalcTest.php"))
    assert a.is_test_file(Path("tests/CalcTest.php"))
    assert a.is_test_file(Path("tests/Helper.php"))
    assert a.is_test_file(Path("spec/CalcTest.php"))
    assert not a.is_test_file(Path("src/Calc.php"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_php_adapter_analyze_phpunit(tmp_path):
    f = tmp_path / "FibTest.php"
    f.write_text(PHP_TESTS, encoding="utf-8")
    info = PhpAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 3


def test_php_adapter_analyze_attribute_style(tmp_path):
    f = tmp_path / "AttrTest.php"
    f.write_text(
        "<?php\n"
        "use PHPUnit\\Framework\\Attributes\\Test;\n"
        "final class AttrTest\n"
        "{\n"
        "    #[Test]\n"
        "    public function adds(): void\n"
        "    {\n"
        "        self::assertSame(2, add(1, 1));\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    info = PhpAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] >= 1


def test_php_adapter_analyze_empty(tmp_path):
    f = tmp_path / "EmptyTest.php"
    f.write_text("<?php\nfinal class EmptyTest {}\n", encoding="utf-8")
    info = PhpAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

@needs_php
def test_php_adapter_check_syntax_with_php(tmp_path):
    f = tmp_path / "fib.php"
    f.write_text(PHP_IMPL, encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


@needs_php
def test_php_adapter_check_syntax_reports_error(tmp_path):
    f = tmp_path / "broken.php"
    f.write_text("<?php\nfunction fib( {\n", encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is False
    assert "PHP 语法错误" in msg


def test_php_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "empty.php"
    f.write_text("", encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is False and "空文件" in msg


def test_php_adapter_check_syntax_missing_php(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    f = tmp_path / "x.php"
    f.write_text("<?php\n", encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is False and "PHP" in msg


# ---------- 测试命令识别 ----------

def test_php_adapter_identify_test_command():
    a = PhpAdapter()
    assert a.identify_test_command("phpunit")
    assert a.identify_test_command("vendor/bin/phpunit")
    assert a.identify_test_command("./vendor/bin/phpunit --testsuite unit")
    assert a.identify_test_command("composer test")
    assert a.identify_test_command("php vendor/bin/phpunit")
    assert not a.identify_test_command("php -l src/fib.php")
    assert not a.identify_test_command("ls -la")


# ---------- 输出解析 ----------

def test_php_parse_passed_ok_line():
    out = "OK (3 tests, 5 assertions)\n"
    ok, summary = PhpAdapter().parse_test_output(out, 0)
    assert ok is True and "3" in summary and "5" in summary


def test_php_parse_passed_summary():
    out = "Tests: 3, Assertions: 5, Failures: 0, Errors: 0\n"
    ok, summary = PhpAdapter().parse_test_output(out, 0)
    assert ok is True and "Assertions: 5" in summary


def test_php_parse_failed_summary():
    out = "Tests: 3, Assertions: 5, Failures: 1, Errors: 0\n"
    ok, summary = PhpAdapter().parse_test_output(out, 1)
    assert ok is False and "Failures: 1" in summary


def test_php_parse_failures_flag():
    # 摘要行优先：即便同时出现 FAILURES! 标记，也以 Tests:/Failures: 汇总为准
    out = "FAILURES!\nTests: 2, Assertions: 2, Failures: 1, Errors: 0\n"
    ok, summary = PhpAdapter().parse_test_output(out, 1)
    assert ok is False and "Failures: 1" in summary


def test_php_parse_failures_flag_only():
    # 无汇总行但出现 FAILURES! / ERRORS! 标记时按标记判定
    out = "There were 2 failures:\n\n1) FibTest::testBase\nFAILURES!\n"
    ok, summary = PhpAdapter().parse_test_output(out, 1)
    assert ok is False and "FAILURES!" in summary


def test_php_parse_unknown_failure():
    ok, summary = PhpAdapter().parse_test_output("", 7)
    assert ok is False and "7" in summary


def test_php_parse_passed_fallback():
    ok, summary = PhpAdapter().parse_test_output("build finished", 0)
    assert ok is True and "所有测试通过" in summary


# ---------- 输出解析 / 工具缺失边界（v0.28.0 覆盖率门禁） ----------

def test_php_decode_output_none():
    from anti_shortcut.languages.php import _decode_output

    assert _decode_output(None) == ""


def test_php_decode_output_fallback_latin1():
    from anti_shortcut.languages.php import _decode_output

    assert _decode_output(b"\x81") == "\x81"


def test_php_check_syntax_mocked_ok(tmp_path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(shutil, "which", lambda name: "php")
    monkeypatch.setattr(
        "anti_shortcut.languages.php.subprocess.run",
        lambda cmd, **kw: sp.CompletedProcess(cmd, 0, stdout=b"No syntax errors detected", stderr=b""),
    )
    f = tmp_path / "fib.php"
    f.write_text("<?php\n", encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is True and "语法检查通过" in msg


def test_php_check_syntax_mocked_error(tmp_path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(shutil, "which", lambda name: "php")
    monkeypatch.setattr(
        "anti_shortcut.languages.php.subprocess.run",
        lambda cmd, **kw: sp.CompletedProcess(cmd, 1, stdout=b"", stderr=b"PHP Parse error:  syntax error in fib.php"),
    )
    f = tmp_path / "broken.php"
    f.write_text("<?php\nfunction fib( {\n", encoding="utf-8")
    ok, msg = PhpAdapter().check_syntax(f)
    assert ok is False and "PHP 语法错误" in msg
