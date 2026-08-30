"""Ruby 语言适配器（v0.11.0）：ruby -c 语法检查 + RSpec / Minitest 测试统计与输出解析。

- 文件识别：``*.rb`` / ``app/**`` / ``lib/**`` 为实现；``*_spec.rb`` / ``spec/**`` /
  ``test/**`` 为测试（RSpec / Minitest 约定）
- 语法检查：调用 ``ruby -c <file>``（输出 ``Syntax OK``）；工具缺失时返回明确错误
- 测试统计：RSpec ``describe`` / ``it`` / ``specify`` + ``expect(...).to`` 断言，
  Minitest ``def test_*`` + ``assert_*``（启发式）
- 测试命令：``rspec`` / ``bundle exec rspec`` / ``rake test`` / ``ruby -Itest`` / ``rails test``
- 输出解析：RSpec ``N examples, M failures`` / Minitest ``N runs, M assertions, F failures``
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["RubyAdapter"]

_RSPEC_DECL_RE = re.compile(
    r"^\s*(RSpec\.)?describe\b|^\s*context\b|^\s*(it|specify|example)\b",
    re.M,
)
_RSPEC_ASSERT_RE = re.compile(
    r"\bexpect\s*\(|\.to\s|\.not_to\s|\.to_not\s|\bshould\b|\bmust\b|\bassert_",
    re.M,
)
_MINITEST_DEF_RE = re.compile(r"^\s*def\s+test_[A-Za-z0-9_]+", re.M)
_MINITEST_ASSERT_RE = re.compile(
    r"\bassert(_equal|_nil|_not_nil|_true|_false|_raises|_match|_in_delta|_empty|_includes)?\s*\(",
    re.M,
)

_RSPEC_SUMMARY_RE = re.compile(r"(\d+)\s+examples?,\s*(\d+)\s+failures?", re.M)
_MINITEST_SUMMARY_RE = re.compile(
    r"(\d+)\s+runs?,\s*(\d+)\s+assertions?,\s*(\d+)\s+failures?",
    re.M,
)
_RSPEC_FAIL_NAME_RE = re.compile(r"^\s*\d+\)\s+(.+)$", re.M)


def _decode_output(raw: bytes | None) -> str:
    """解码 ruby -c / rspec 输出：优先 UTF-8，回退 GBK（中文 Windows）/ cp1252。"""
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_ruby_errors(output: str) -> list[str]:
    """提取 ruby -c 输出的语法错误行。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if re.search(r"(syntax error|unexpected|expecting|unterminated|Invalid)", stripped, re.IGNORECASE):
            out.append(stripped)
    return out


class RubyAdapter(LanguageAdapter):
    """Ruby：``ruby -c`` 语法检查 + RSpec / Minitest 启发式测试统计。"""

    name = "ruby"
    file_extensions = [".rb"]
    source_file_patterns = ["*.rb", "app/**/*.rb", "lib/**/*.rb", "config/**/*.rb", "db/**/*.rb"]
    test_file_patterns = [
        "*_spec.rb",
        "**/*_spec.rb",
        "spec/**/*_spec.rb",
        "*_test.rb",
        "**/*_test.rb",
        "test/**/*_test.rb",
    ]
    test_command_patterns = [
        r"^\s*(bundle\s+exec\s+)?rspec\b",
        r"^\s*(bundle\s+exec\s+)?rake\s+test\b",
        r"^\s*(bundle\s+exec\s+)?rails\s+test\b",
        r"^\s*bin/rails\s+test\b",
        r"^\s*(bundle\s+exec\s+)?ruby\s+(-Itest\b|-I\s+test\b)",
        r"^\s*(bundle\s+exec\s+)?ruby\s+\S*(_test|_spec)\.rb\b",
        r"^\s*minitest\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        ruby = shutil.which("ruby")
        if ruby is None:
            return False, (
                f"未检测到 Ruby（ruby），无法对 {path.name} 做语法检查；"
                "请安装 Ruby（https://www.ruby-lang.org/），或在配置中改用其他语言适配器"
            )
        proc = subprocess.run([ruby, "-c", str(path)], capture_output=True)
        if proc.returncode == 0:
            return True, "语法检查通过（ruby -c）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _extract_ruby_errors(stderr)
        first = errors[0] if errors else stderr.strip()
        return False, f"Ruby 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        rspec_decls = [m.group(0).strip() for m in _RSPEC_DECL_RE.finditer(text)]
        minitest_defs = _MINITEST_DEF_RE.findall(text)
        rspec_asserts = len(_RSPEC_ASSERT_RE.findall(text))
        minitest_asserts = len(_MINITEST_ASSERT_RE.findall(text))
        tests: list[dict[str, Any]] = []
        # RSpec：it / specify / example 块计为用例；describe / context 为分组
        for i, decl in enumerate(rspec_decls):
            if re.search(r"^\s*(it|specify|example)\b", decl):
                tests.append({"name": f"<{i + 1}:{decl[:40]}>", "assertions": 0, "heuristic": True})
        for i, name in enumerate(minitest_defs):
            tests.append({"name": f"<{i + 1}:{name.strip()}>", "assertions": 0, "heuristic": True})
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": rspec_asserts + minitest_asserts,
        }

    # ---------- 测试输出解析（RSpec / Minitest） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 RSpec / Minitest 输出：``N examples, M failures`` / ``N runs, M assertions``。"""
        text = output or ""
        if exit_code == 0:
            m = _RSPEC_SUMMARY_RE.search(text)
            if m and m.group(2) == "0":
                return True, f"{m.group(1)} examples, 0 failures"
            m2 = _MINITEST_SUMMARY_RE.search(text)
            if m2 and m2.group(3) == "0":
                return True, f"{m2.group(1)} runs, {m2.group(2)} assertions, 0 failures"
            if "0 failures" in text:
                return True, "所有测试通过（RSpec / Minitest）"
            return True, "所有测试通过"
        m = _RSPEC_SUMMARY_RE.search(text)
        if m:
            names = "、".join(dict.fromkeys(_RSPEC_FAIL_NAME_RE.findall(text)))[:400]
            suffix = f"（失败用例: {names}）" if names else ""
            return False, f"RSpec 失败：{m.group(1)} examples, {m.group(2)} failures{suffix}"
        m2 = _MINITEST_SUMMARY_RE.search(text)
        if m2:
            return False, (
                f"Minitest 失败：{m2.group(1)} runs, {m2.group(2)} assertions, "
                f"{m2.group(3)} failures"
            )
        if "Failure:" in text or "Error:" in text:
            return False, "测试失败（RSpec / Minitest 存在失败或错误）"
        return False, f"测试失败，退出码 {exit_code}"
