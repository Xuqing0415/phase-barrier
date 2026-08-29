"""Java 语言适配器（v0.4.0）：javac 语法检查 + JUnit 注解启发式测试统计。

- 文件识别：``*.java`` 为实现；``*Test.java`` / ``*Tests.java`` / ``src/test/**`` 为测试
- 语法检查：优先项目级编译（``mvn test-compile`` / ``gradle compileTestJava``，
  优先 ``mvnw`` / ``gradlew`` 包装器，带指纹缓存避免重复编译）；无构建文件或
  构建工具缺失时回退单文件 ``javac -proc:none -d <tmp>``
- 依赖容错：单文件检查无法解析跨文件依赖，``cannot find symbol`` /
  ``package does not exist`` 等仅降级为“通过（需完整项目编译验证）”，
  真正的语法错误（``';' expected`` 等）仍会被拒绝；项目级编译以真实结果为准
- 测试统计：按 ``@Test`` 注解数量 + JUnit/Hamcrest 断言关键字（启发式）
- 测试命令：``mvn test`` / ``gradle test`` / ``./mvnw test`` / ``./gradlew test`` 等
- 输出解析：Maven/Gradle 风格 ``Tests run: N, Failures: M`` / ``BUILD SUCCESS``

语法检查依赖外部工具（JDK）；缺失时校验失败并提示安装，不会静默放行。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["JavaAdapter"]

# JUnit 4/5 + Hamcrest 常用断言关键字（启发式，不含 Mockito 的 verify）
_JUNIT_ASSERT_RE = re.compile(
    r"\b(assertEquals|assertNotEquals|assertArrayEquals|assertTrue|assertFalse|"
    r"assertNull|assertNotNull|assertSame|assertNotSame|assertThrows|assertTimeout|"
    r"assertTimeoutPreemptively|assertAll|assertThat|fail)\b",
    re.M,
)
_TEST_ANNOTATION_RE = re.compile(r"^\s*@Test\b", re.M)

# javac 对“单文件无法解析依赖”的典型报错：这些不算语法错误
_DEPENDENCY_MARKERS = (
    "cannot find symbol",
    "cannot access",
    "cannot find class",
    "does not exist",  # 匹配 "package com.x does not exist" / "module x does not exist"
    "程序包",
    "找不到符号",
)

_JAVAC_OUT_DIR = ".phase-barrier-javac"

_TEST_RUN_RE = re.compile(
    r"Tests run:\s*\d+(?:,\s*Failures:\s*\d+)?(?:,\s*Errors:\s*\d+)?"
)


def _decode_output(raw: bytes | None) -> str:
    """解码 javac 输出：优先 UTF-8（Linux），回退 GBK（中文 Windows）/ cp1252。"""
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_javac_errors(output: str) -> list[str]:
    """提取 javac 输出的 error 行（去掉行号前缀，保留可读信息）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if "error:" in stripped or "错误:" in stripped:
            out.append(stripped)
    return out


class JavaAdapter(LanguageAdapter):
    """Java：项目级编译 / ``javac`` 语法检查 + ``@Test`` 注解启发式测试统计。"""

    name = "java"
    file_extensions = [".java"]
    source_file_patterns = ["src/**/*.java", "*.java"]
    test_file_patterns = ["*Test.java", "*Tests.java", "src/test/**/*.java"]
    test_command_patterns = [
        r"^\s*mvn\s+test\b",
        r"^\s*(\./)?mvnw\s+test\b",
        r"^\s*gradle\s+test\b",
        r"^\s*(\./)?gradlew\s+test\b",
        r"^\s*java\s+-jar\s+.*junit-platform-console-standalone\.jar\b",
    ]

    def __init__(self) -> None:
        super().__init__()
        # 项目级编译结果缓存：key = (项目根, 源文件指纹)，文件变化自动失效
        self._project_check_cache: dict[tuple[Any, ...], tuple[bool, str]] = {}

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        root = self._find_project_root(path)
        build_cmd = self._project_compile_command(root) if root is not None else None
        if build_cmd is not None:
            return self._check_project(root, build_cmd)
        javac = shutil.which("javac")
        if not javac:
            return False, (
                f"未检测到 JDK（javac），无法对 {path.name} 做语法检查；"
                "请安装 JDK，或在配置中改用其他语言适配器"
            )
        # javac 输出目录优先放在源文件旁的隐藏目录：工作区必然可写，
        # 避免系统临时目录在 Windows 上因 ACL 限制导致写入 .class 失败。
        # 工作区只读（如只读挂载）时回退到系统临时目录。
        out_dir = path.parent / _JAVAC_OUT_DIR
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            out_dir = None
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="phase-barrier-javac-"))
        try:
            proc = self._run_javac(javac, out_dir, path)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
        if proc.returncode == 0:
            return True, "语法检查通过（javac）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _extract_javac_errors(stderr)
        if errors and all(
            any(marker in err for marker in _DEPENDENCY_MARKERS) for err in errors
        ):
            return True, (
                f"语法检查通过（javac，仅存在未解析的跨文件依赖，"
                "需在完整项目中编译验证）"
            )
        first = errors[0] if errors else stderr.strip()
        return False, f"Java 语法错误: {first[:500]}"

    # ---------- 项目级编译 ----------

    @staticmethod
    def _find_project_root(path: Path) -> Path | None:
        """从文件所在目录向上查找包含 pom.xml / build.gradle* 的项目根。"""
        cur = path if path.is_absolute() else Path(path).resolve()
        for d in (cur.parent, *cur.parents):
            if (
                (d / "pom.xml").is_file()
                or (d / "build.gradle").is_file()
                or (d / "build.gradle.kts").is_file()
            ):
                return d
        return None

    @staticmethod
    def _project_compile_command(root: Path) -> list[str] | None:
        """返回项目级编译命令；无可用构建工具时返回 None（回退单文件 javac）。

        优先级：``mvnw`` / ``gradlew`` 包装器 > PATH 上的 ``mvn`` / ``gradle``。
        """
        if (root / "pom.xml").is_file():
            mvnw = root / ("mvnw.cmd" if os.name == "nt" else "mvnw")
            if mvnw.is_file() and (os.name == "nt" or os.access(str(mvnw), os.X_OK)):
                return [str(mvnw), "-q", "test-compile"]
            mvn = shutil.which("mvn")
            if mvn:
                return [mvn, "-q", "test-compile"]
            return None
        gradlew = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if gradlew.is_file() and (os.name == "nt" or os.access(str(gradlew), os.X_OK)):
            return [str(gradlew), "compileTestJava", "--console=plain", "-q"]
        gradle = shutil.which("gradle")
        if gradle:
            return [gradle, "compileTestJava", "--console=plain", "-q"]
        return None

    @staticmethod
    def _project_fingerprint(root: Path) -> tuple[int, int]:
        """项目 .java 文件的 (最新修改时间, 文件数) 指纹，用于缓存失效。"""
        max_mtime = 0
        count = 0
        for p in root.rglob("*.java"):
            if any(part in (".agent_gate", ".git", "target", "build") for part in p.parts):
                continue
            try:
                st = p.stat()
                max_mtime = max(max_mtime, st.st_mtime_ns)
            except OSError:
                continue
            count += 1
        return (max_mtime, count)

    def _check_project(self, root: Path, cmd: list[str]) -> tuple[bool, str]:
        """运行项目级编译（带指纹缓存），返回 (是否通过, 错误信息)。"""
        key = (str(root), self._project_fingerprint(root))
        if key in self._project_check_cache:
            return self._project_check_cache[key]
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            result = (
                True,
                f"语法检查通过（项目级编译：{_command_label(cmd)}）",
            )
        else:
            output = (proc.stderr or "") + (proc.stdout or "")
            errors = _extract_build_errors(output)
            first = errors[0] if errors else output.strip()[:500]
            result = (False, f"项目编译错误: {first[:500]}")
        self._project_check_cache[key] = result
        return result

    @staticmethod
    def _run_javac(
        javac: str, out_dir: Path, path: Path
    ) -> subprocess.CompletedProcess:
        # 捕获原始字节：Windows 上 javac 输出可能为 GBK，交由 _decode_output 解码
        return subprocess.run(
            [javac, "-proc:none", "-d", str(out_dir), str(path)],
            capture_output=True,
        )

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        test_count = len(_TEST_ANNOTATION_RE.findall(text))
        assertions_total = len(_JUNIT_ASSERT_RE.findall(text))
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

    # ---------- 测试输出解析（Maven / Gradle） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 Maven/Gradle 风格输出：``Tests run: N, Failures: M`` / ``BUILD SUCCESS``。"""
        text = output or ""
        if exit_code == 0:
            summary = _extract_test_summary(text)
            return True, summary or "所有测试通过"
        m = re.search(
            r"Tests run:\s*(\d+)(?:,\s*Failures:\s*(\d+))?(?:,\s*Errors:\s*(\d+))?",
            text,
        )
        if m:
            failures = int(m.group(2) or 0)
            errors = int(m.group(3) or 0)
            ok = failures == 0 and errors == 0
            summary = f"Tests run: {m.group(1)}, Failures: {failures}, Errors: {errors}"
            return ok, summary
        if "BUILD FAILURE" in text or "BUILD FAILED" in text:
            return False, "BUILD FAILURE"
        return False, f"测试失败，退出码 {exit_code}"


def _command_label(cmd: list[str]) -> str:
    """从编译命令中提取人类可读的标签（mvn test-compile / gradle compileTestJava）。"""
    joined = " ".join(cmd)
    if "test-compile" in joined or " mvn" in joined:
        return "mvn test-compile"
    if "compileTestJava" in joined:
        return "gradle compileTestJava"
    return "build"


def _extract_build_errors(output: str) -> list[str]:
    """提取 Maven / Gradle 编译输出中的 error 行（去掉行号前缀，保留可读信息）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if ".java:" in stripped and ("[ERROR]" in stripped or "error:" in stripped or "错误:" in stripped):
            out.append(stripped)
        elif "[ERROR]" in stripped and ("COMPILATION" in stripped or "cannot find" in stripped or "does not exist" in stripped):
            out.append(stripped)
    return out


def _extract_test_summary(text: str) -> str:
    """从 Maven/Gradle 输出中提取测试统计摘要行。"""
    m = _TEST_RUN_RE.search(text)
    if m:
        return m.group(0)
    if "BUILD SUCCESSFUL" in text or "BUILD SUCCESS" in text:
        return "BUILD SUCCESS"
    return ""