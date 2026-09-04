"""Scala 语言适配器（v0.34.0）：scalac 语法检查 + JUnit / ScalaTest / MUnit 启发式测试统计。

- 文件识别：``*.scala`` / ``src/main/scala/**`` 为实现；测试文件为 ``*Test.scala`` /
  ``*Tests.scala`` / ``*Spec.scala`` / ``src/test/**`` / ``test/**``
- 语法检查：``scalac -d <tmp> <file>`` 单文件检查；跨文件/库依赖缺失（not found /
  unresolved / is not a member 等）降级为“通过（需完整项目编译验证）”，真正的语法
  错误会被拒绝；scalac 缺失返回明确错误，不静默放行
- 测试统计：``@Test`` 注解 + ScalaTest / MUnit ``test("...")`` / spec2
  ``"..." should`` 风格统计，断言关键字计数（启发式）
- 测试命令：复用 Java 适配器（mvn / gradle / testng）并补充 ``sbt test`` /
  ``scala-cli test``
- 输出解析：ScalaTest 汇总（``Tests: succeeded N, failed M`` / ``All tests passed.``），
  其余回退 Java 适配器（Surefire / Gradle / JUnit Platform Console）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .java import JavaAdapter

__all__ = ["ScalaAdapter"]

_SCALA_TEST_ANNOTATION_RE = re.compile(r"^\s*@Test\b", re.M)
_SCALA_FUNSUITE_TEST_RE = re.compile(r'^\s*test\s*\(\s*"', re.M)
_SCALA_SPEC2_EXAMPLE_RE = re.compile(r'^\s*".*"\s+should\s+', re.M)
_SCALA_ASSERT_RE = re.compile(
    r"\bassert\w*\s*[({]|\bassert\w*\s*\{|\bintercept\s*[\[\(]"
    r"|\bshould\s+be\b|\bshouldEqual\b|\bshouldBe\b|\bmust\s+be\b|\bfail\s*\(",
    re.M,
)

_SCALAC_OUT_DIR = ".phase-barrier-scalac"

# scalac 对“单文件无法解析跨文件/库符号”的典型报错：仅依赖缺失不算语法错误
_SCALAC_DEPENDENCY_MARKERS = (
    "not found",
    "unresolved",
    "does not exist",
    "is not a member",
    "not a member",
    "missing",
)

# ScalaTest 汇总：``Tests: succeeded 3, failed 0, canceled 0, ignored 0, pending 0``
_SCALATEST_SUMMARY_RE = re.compile(
    r"Tests:\s+succeeded\s+(?P<ok>\d+),\s+failed\s+(?P<fail>\d+)",
    re.M,
)
_SCALATEST_TOTAL_RE = re.compile(r"Total number of tests run:\s*(\d+)", re.M)


def _decode_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _scalac_errors(stderr: str) -> list[str]:
    """提取 scalac 报错行（含 ``error`` 关键字的行）。"""
    return [ln.strip() for ln in stderr.splitlines() if "error" in ln.lower()]


class ScalaAdapter(JavaAdapter):
    """Scala：scalac 语法检查 + JUnit / ScalaTest / MUnit 启发式测试统计。"""

    name = "scala"
    file_extensions = [".scala"]
    source_file_patterns = [
        "src/main/scala/**/*.scala",
        "src/**/*.scala",
        "**/*.scala",
        "*.scala",
    ]
    test_file_patterns = [
        "*Test.scala",
        "*Tests.scala",
        "*Spec.scala",
        "**/*Test.scala",
        "**/*Tests.scala",
        "**/*Spec.scala",
        "src/test/**/*.scala",
        "**/src/test/**/*.scala",
        "test/**/*.scala",
    ]
    test_command_patterns = list(JavaAdapter.test_command_patterns) + [
        r"^\s*(\./|\.\\)?sbt\s+test\b",
        r"^\s*scala-cli\s+test\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        scalac = shutil.which("scalac")
        if not scalac:
            return False, (
                f"未检测到 Scala 编译器（scalac），无法对 {path.name} 做语法检查；"
                "请安装 Scala（如 sdk install scala / 官方 scala-2.13.x 发行包），"
                "或在配置中显式指定其他语言适配器"
            )
        out_dir = path.parent / _SCALAC_OUT_DIR
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            out_dir = None
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="phase-barrier-scalac-"))
        try:
            proc = subprocess.run(
                [scalac, "-d", str(out_dir), str(path)],
                capture_output=True,
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
        if proc.returncode == 0:
            return True, "语法检查通过（scalac）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _scalac_errors(stderr)
        if errors and all(
            any(marker in err for marker in _SCALAC_DEPENDENCY_MARKERS)
            for err in errors
        ):
            return True, (
                "语法检查通过（scalac，仅存在未解析的跨文件/依赖引用，"
                "需在完整项目中编译验证）"
            )
        first = errors[0] if errors else stderr.strip()
        return False, f"Scala 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        annotation_count = len(_SCALA_TEST_ANNOTATION_RE.findall(text))
        suite_count = len(_SCALA_FUNSUITE_TEST_RE.findall(text)) + len(
            _SCALA_SPEC2_EXAMPLE_RE.findall(text)
        )
        assertions_total = len(_SCALA_ASSERT_RE.findall(text))
        tests = [
            {"name": f"<{i + 1}:test>", "assertions": 0, "heuristic": True}
            for i in range(annotation_count + suite_count)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（ScalaTest 优先，其余回退 Java） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        text = output or ""
        m = _SCALATEST_SUMMARY_RE.search(text)
        if m:
            failed = int(m.group("fail"))
            ok = failed == 0 and exit_code == 0
            total = _SCALATEST_TOTAL_RE.search(text)
            suffix = f"，共 {total.group(1)} 个" if total else ""
            return ok, f"ScalaTest: succeeded {m.group('ok')}, failed {failed}{suffix}"
        if "TEST FAILED" in text or "*** FAILED ***" in text:
            return False, "ScalaTest 有测试失败"
        if exit_code == 0 and "All tests passed" in text:
            return True, "ScalaTest 全部通过"
        return super().parse_test_output(output, exit_code)