"""Dart 语言适配器（v0.40.0）：dart format 语法检查 + package:test 启发式测试统计。

- 文件识别：``*.dart`` 为实现（``lib/**`` / ``bin/**`` / ``web/**`` 等源根）；测试文件为
  ``*_test.dart`` / ``test/**`` / ``integration_test/**``（Flutter）
- 语法检查：``dart format --output=none <file>``——只做解析不落盘，语法错误返回非零并给出
  行号/位置；Dart SDK 缺失返回明确错误，不静默放行
- 测试统计：package:test 风格 ``test()`` / ``testWidgets()`` 声明与 ``expect()`` 断言
  （启发式），统计前剥离注释（``//``、``/* */`` 嵌套）与字符串字面量
- 测试命令：``dart test`` / ``flutter test`` / ``dart run test`` / ``pub run test``
- 输出解析：``dart test`` 进度汇总 ``00:01 +5: All tests passed!`` 与
  ``00:05 +2 -1: Some tests failed.``（支持 ``~N`` 跳过计数与 ``-N`` 失败计数）
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["DartAdapter"]

# package:test 测试声明：test('描述', ...) / testWidgets('描述', ...)（Flutter）
_DART_TEST_CALL_RE = re.compile(r"\btest(?:Widgets)?\s*\(", re.M)
# 断言：expect / expectLater / expectAsync
_DART_ASSERT_RE = re.compile(
    r"\bexpect(?:Later|Async|Async0|Async1|Async2)?\s*\(", re.M
)

# dart test 最终汇总行（进度行不以状态结尾，只有末行匹配）：
#   00:01 +5: All tests passed!
#   00:02 +3 ~1: All tests passed!
#   00:05 +2 -1: Some tests failed.
_DART_FINAL_LINE_RE = re.compile(
    r"(?:\d+:\d+\s+)?\+(?P<passed>\d+)"
    r"(?:\s+-(?P<failed>\d+))?(?:\s+~(?P<skipped>\d+))?:"
    r"\s*(?P<status>All tests passed!|Some tests failed\.)\s*$",
    re.M,
)


def _decode_output(raw: bytes | None) -> str:
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _strip_dart_comments_strings(text: str) -> str:
    """移除 Dart 注释与字符串字面量，避免注释/字符串中的 test/expect 被误判。

    覆盖：``//`` 行注释、``/* */``（支持嵌套）、单引号/双引号字符串、
    三引号字符串与原始字符串（``r'...'`` / ``r"..."``）。
    注释与字符串统一替换为空格，保持行列结构近似稳定。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
            continue
        if ch == "/" and nxt == "*":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if text[j : j + 2] == "/*":
                    depth += 1
                    j += 2
                elif text[j : j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            i = j
            out.append(" ")
            continue
        if ch in ("'", '"'):
            raw = i > 0 and text[i - 1] in ("r", "R")
            triple = text[i : i + 3] in ("'''", '"""')
            quote = ch
            j = i + (3 if triple else 1)
            while j < n:
                if text[j] == "\\" and not raw:
                    j += 2
                    continue
                if triple:
                    if text[j : j + 3] == quote * 3:
                        j += 3
                        break
                elif text[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class DartAdapter(LanguageAdapter):
    """Dart：``dart format`` 语法检查 + package:test 启发式测试统计。"""

    name = "dart"
    file_extensions = [".dart"]
    source_file_patterns = [
        "*.dart",
        "**/*.dart",
        "lib/**/*.dart",
        "bin/**/*.dart",
        "web/**/*.dart",
        "tool/**/*.dart",
    ]
    test_file_patterns = [
        "*_test.dart",
        "**/*_test.dart",
        "test/**/*.dart",
        "**/test/**/*.dart",
        "integration_test/**/*.dart",
        "**/integration_test/**/*.dart",
    ]
    test_command_patterns = [
        r"^\s*dart\s+test\b",
        r"^\s*dart\s+run\s+test\b",
        r"^\s*flutter\s+test\b",
        r"^\s*pub\s+run\s+test\b",
        r"^\s*fvm\s+dart\s+test\b",
        r"^\s*fvm\s+flutter\s+test\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        dart = shutil.which("dart")
        if dart is None:
            return False, (
                f"未检测到 Dart SDK（dart），无法对 {path.name} 做语法检查；"
                "请安装 Dart（https://dart.dev/get-dart），或在配置中改用其他语言适配器"
            )
        proc = subprocess.run(
            [dart, "format", "--output=none", str(path)], capture_output=True
        )
        if proc.returncode == 0:
            return True, "语法检查通过（dart format 解析）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
        first = lines[0] if lines else stderr.strip()
        return False, f"Dart 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_dart_comments_strings(text)
        calls = _DART_TEST_CALL_RE.findall(cleaned)
        assertions_total = len(_DART_ASSERT_RE.findall(cleaned))
        tests = [
            {"name": f"<{i + 1}:test>", "assertions": 0, "heuristic": True}
            for i in range(len(calls))
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（dart test / flutter test） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        text = output or ""
        m = _DART_FINAL_LINE_RE.search(text)
        if m:
            status = m.group("status")
            passed = int(m.group("passed") or 0)
            if status.startswith("All"):
                skipped = int(m.group("skipped") or 0)
                suffix = f"，{skipped} 个跳过" if skipped else ""
                return True, f"Dart 测试通过：{passed} 个测试全部通过{suffix}"
            failed = int(m.group("failed") or 0)
            return False, f"Dart 测试失败：{passed} 通过 / {failed} 未通过"
        if exit_code == 0:
            return True, "所有测试通过（dart test）"
        return False, f"测试失败，退出码 {exit_code}"