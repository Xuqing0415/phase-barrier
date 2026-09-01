"""PHP 语言适配器（v0.28.0）：php -l 语法检查 + PHPUnit 启发式测试统计。

- 文件识别：``*.php`` / ``src/**`` / ``app/**`` 为实现；
  测试文件为 ``*Test.php`` / ``tests/**`` / ``test/**`` / ``spec/**``
- 语法检查：``php -l <file>``（PHP CLI 缺失返回明确错误，不静默放行）
- 测试统计：PHPUnit 风格 ``public function testXxx`` 方法 +
  PHPUnit 10+ 属性 ``#[Test]`` 数量，断言 ``assert*()`` / ``expectException()``（启发式）
- 测试命令：``phpunit`` / ``vendor/bin/phpunit`` / ``composer test`` 等
- 输出解析：PHPUnit ``OK (N tests, M assertions)``、
  ``Tests: N, Assertions: M, Failures: X, Errors: Y`` 与 ``FAILURES!`` / ``ERRORS!``
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["PhpAdapter"]

# PHPUnit 测试方法：``public function testXxx()``；PHPUnit 10+ 还支持 ``#[Test]`` 属性
_PHP_TEST_METHOD_RE = re.compile(r"^\s*public\s+function\s+(test\w+)\s*\(", re.M)
_PHP_TEST_ATTRIBUTE_RE = re.compile(r"^\s*#\[(?:\w+\\)*Test\]", re.M)
# PHPUnit / PHPUnit 断言：assert*() 调用与 expectException()
_PHP_ASSERT_RE = re.compile(r"\bassert\w*\s*\(|\bexpectException(?:WithMessage|WithMessageMatches)?\s*\(", re.M)

# PHPUnit 输出：
# - OK (3 tests, 5 assertions)
# - Tests: 3, Assertions: 5, Failures: 1, Errors: 0
# - FAILURES! / ERRORS! / WARNINGS!
_PHPUNIT_OK_COUNT_RE = re.compile(r"^\s*OK\s*\(\s*(\d+)\s+tests(?:,\s*(\d+)\s+assertions)?", re.M)
_PHPUNIT_SUMMARY_RE = re.compile(
    r"Tests:\s*(?P<run>\d+)(?:,\s*Assertions:\s*(?P<assert>\d+))?"
    r"(?:,\s*Failures:\s*(?P<fail>\d+))?(?:,\s*Errors:\s*(?P<err>\d+))?"
    r"(?:,\s*Warnings:\s*(?P<warn>\d+))?",
    re.M,
)
_PHPUNIT_FAILURES_RE = re.compile(r"\b(FAILURES!|ERRORS!)", re.M)


def _decode_output(raw: bytes | None) -> str:
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


class PhpAdapter(LanguageAdapter):
    """PHP：``php -l`` 语法检查 + PHPUnit 启发式测试统计。"""

    name = "php"
    file_extensions = [".php"]
    source_file_patterns = [
        "*.php",
        "**/*.php",
        "src/**/*.php",
        "app/**/*.php",
        "lib/**/*.php",
        "public/**/*.php",
    ]
    test_file_patterns = [
        "*Test.php",
        "**/*Test.php",
        "tests/**/*.php",
        "**/tests/**/*.php",
        "test/**/*.php",
        "spec/**/*.php",
    ]
    test_command_patterns = [
        r"^\s*phpunit\b",
        r"^\s*vendor/bin/phpunit\b",
        r"^\s*\.?/?vendor/bin/phpunit\b",
        r"^\s*composer\s+test\b",
        r"^\s*php\s+[^\n]*(?:phpunit|vendor/bin/phpunit)\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        php = shutil.which("php")
        if php is None:
            return False, (
                f"未检测到 PHP CLI（php），无法对 {path.name} 做语法检查；"
                "请安装 PHP（https://www.php.net/downloads），"
                "或在配置中改用其他语言适配器"
            )
        proc = subprocess.run([php, "-l", str(path)], capture_output=True)
        if proc.returncode == 0:
            return True, "语法检查通过（php -l）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
        first = lines[0] if lines else stderr.strip()
        return False, f"PHP 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        methods = _PHP_TEST_METHOD_RE.findall(text)
        attributes = _PHP_TEST_ATTRIBUTE_RE.findall(text)
        assertions_total = len(_PHP_ASSERT_RE.findall(text))
        names = list(methods) + [f"<attr#{i + 1}>" for i in range(len(attributes))]
        tests = [
            {"name": f"<{i + 1}:{name}>", "assertions": 0, "heuristic": True}
            for i, name in enumerate(names)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（PHPUnit） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        text = output or ""
        if exit_code == 0:
            m = _PHPUNIT_OK_COUNT_RE.search(text)
            if m:
                suffix = f"，{m.group(2)} 条断言" if m.group(2) else ""
                return True, f"PHPUnit 通过：{m.group(1)} 个测试全部通过{suffix}"
            m = _PHPUNIT_SUMMARY_RE.search(text)
            if m:
                return True, (
                    f"PHPUnit 通过：Tests: {m.group('run')}, "
                    f"Assertions: {m.group('assert') or 0}"
                )
            return True, "所有测试通过（PHPUnit）"
        m = _PHPUNIT_SUMMARY_RE.search(text)
        if m:
            fail = int(m.group("fail") or 0)
            err = int(m.group("err") or 0)
            return False, (
                f"PHPUnit 失败：Tests: {m.group('run')}, "
                f"Failures: {fail}, Errors: {err}"
            )
        if _PHPUNIT_FAILURES_RE.search(text):
            return False, "PHPUnit 存在失败或错误（FAILURES! / ERRORS!）"
        return False, f"测试失败，退出码 {exit_code}"
