"""Kotlin 语言适配器（v0.32.0）：kotlinc 语法检查 + JUnit5/kotlin.test 启发式测试统计。

- 文件识别：``*.kt`` / ``src/main/kotlin/**`` 为实现；测试文件为 ``*Test.kt`` /
  ``*Tests.kt`` / ``src/test/**`` / ``test/**`` / ``spec/**``
- 语法检查：``kotlinc -d <tmp> <file>`` 单文件检查；跨文件/库依赖缺失（unresolved
  reference 等）降级为“通过（需完整项目编译验证）”，真正的语法错误会被拒绝；
  kotlinc 缺失返回明确错误，不静默放行
- 测试统计：``@Test`` 注解数量 + JUnit5 / kotlin.test 断言关键字（启发式）
- 测试命令：复用 Java 适配器（``gradle test`` / ``mvn test`` / ``./gradlew test`` 等）
- 输出解析：复用 Java 适配器（Gradle 汇总 / Maven Surefire / JUnit Platform Console）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .java import JavaAdapter

__all__ = ["KotlinAdapter"]

# JUnit5 / kotlin.test 注解风格一致；断言关键字用 \bassert\w*\s*\( 覆盖
# kotlin.test 独有的 assertContentEquals / assertFailsWith / assertIs 等变体
_KOTLIN_TEST_ANNOTATION_RE = re.compile(r"^\s*@Test\b", re.M)
_KOTLIN_ASSERT_RE = re.compile(r"\bassert\w*(?:<[^>]*>)?\s*[({]|\bfail\s*\(", re.M)

_KOTLINC_OUT_DIR = ".phase-barrier-kotlinc"

# kotlinc 对“单文件无法解析跨文件/库符号”的典型报错：仅依赖缺失不算语法错误
_KOTLINC_DEPENDENCY_MARKERS = (
    "unresolved reference",
    "cannot find",
    "does not exist",
    "unresolved",
    "unbound",
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


def _kotlinc_errors(stderr: str) -> list[str]:
    """提取 kotlinc 报错行（``e: path:line:col: msg`` 或含 ``: error:`` 的行）。"""
    return [
        ln.strip()
        for ln in stderr.splitlines()
        if "error" in ln.lower() and (":" in ln)
    ]


class KotlinAdapter(JavaAdapter):
    """Kotlin：kotlinc 语法检查 + JUnit5/kotlin.test 启发式测试统计（复用 Java 输出解析）。"""

    name = "kotlin"
    file_extensions = [".kt"]
    source_file_patterns = [
        "src/main/kotlin/**/*.kt",
        "src/**/*.kt",
        "**/*.kt",
        "*.kt",
    ]
    test_file_patterns = [
        "*Test.kt",
        "*Tests.kt",
        "**/*Test.kt",
        "**/*Tests.kt",
        "src/test/**/*.kt",
        "**/src/test/**/*.kt",
        "test/**/*.kt",
        "spec/**/*.kt",
    ]
    test_command_patterns = list(JavaAdapter.test_command_patterns)

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        kotlinc = shutil.which("kotlinc")
        if not kotlinc:
            return False, (
                f"未检测到 Kotlin 编译器（kotlinc），无法对 {path.name} 做语法检查；"
                "请安装 kotlinc（如 sdk install kotlin / apt install kotlin），"
                "或在配置中显式指定其他语言适配器"
            )
        out_dir = path.parent / _KOTLINC_OUT_DIR
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            out_dir = None
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="phase-barrier-kotlinc-"))
        try:
            proc = subprocess.run(
                [kotlinc, "-d", str(out_dir), str(path)],
                capture_output=True,
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
        if proc.returncode == 0:
            return True, "语法检查通过（kotlinc）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _kotlinc_errors(stderr)
        if errors and all(
            any(marker in err for marker in _KOTLINC_DEPENDENCY_MARKERS)
            for err in errors
        ):
            return True, (
                "语法检查通过（kotlinc，仅存在未解析的跨文件/依赖引用，"
                "需在完整项目中编译验证）"
            )
        first = errors[0] if errors else stderr.strip()
        return False, f"Kotlin 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        test_count = len(_KOTLIN_TEST_ANNOTATION_RE.findall(text))
        assertions_total = len(_KOTLIN_ASSERT_RE.findall(text))
        tests = [
            {"name": f"<{i + 1}:@Test>", "assertions": 0, "heuristic": True}
            for i in range(test_count)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }
