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
- 输出解析：Maven Surefire（``Tests run: N, Failures: M, Errors: K, Skipped: S``）、
  TestNG（``Total tests run: N, Failures: M, Skips: K, Configuration Failures: C``）、
  Gradle（``N tests completed, M failed``，含 ``Class > method() FAILED``与参数化
  ``Class > [N] method(args) FAILED`` 行）、JUnit Platform Console
  （``[ N tests successful / failed ]``）与 ``BUILD SUCCESS / FAILURE``

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

# Maven Surefire 汇总行（含 Skipped；失败时取最后一次出现的 Results 汇总）
_TEST_RUN_AGG_RE = re.compile(
    r"Tests run:\s*(?P<run>\d+)(?:,\s*Failures:\s*(?P<fail>\d+))?"
    r"(?:,\s*Errors:\s*(?P<err>\d+))?(?:,\s*Skipped:\s*(?P<skip>\d+))?"
)
# Gradle：``3 tests completed, 1 failed``
_GRADLE_AGG_RE = re.compile(
    r"\b(?P<n>\d+)\s+tests?\s+completed(?:,\s*(?P<fail>\d+)\s+failed)?"
    r"(?:,\s*(?P<skip>\d+)\s+skipped)?",
    re.IGNORECASE,
)
# JUnit Platform Console：``[ 3 tests successful ]`` / ``[ 1 tests failed ]``
_JUNIT_CONSOLE_AGG_RE = re.compile(
    r"\[\s*(?P<n>\d+)\s+tests?\s+(?P<status>successful|failed|skipped|aborted)\s*\]",
    re.IGNORECASE,
)


# TestNG 汇总：``Total tests run: 5, Failures: 1, Skips: 0, Configuration Failures: 0``
_TESTNG_AGG_RE = re.compile(
    r"Total tests run:\s*(?P<run>\d+)(?:,\s*Failures:\s*(?P<fail>\d+))?"
    r"(?:,\s*Skip(?:s|ped):\s*(?P<skip>\d+))?"
    r"(?:,\s*Configuration Failures:\s*(?P<cfg>\d+))?",
    re.M,
)
# TestNG 失败用例行：``[ERROR] testAdd(com.example.CalcTest)  FAILED``
_TESTNG_FAILURE_RE = re.compile(
    r"^\s*(?:\[ERROR\]\s*)?(\w+\([^)]*\))\s+FAILED\b",
    re.M,
)


# 失败用例提取（v0.23.0）：
# - Surefire：``methodName(ClassName) ... <<< FAILURE!`` / ``<<< ERROR!``
# - Gradle：``com.example.Class > methodName FAILED``
# - JUnit Console：``Failures (N):`` 段中的 ``methodName(ClassName)`` / ``MethodSource [...]``
_JAVA_SUREFIRE_FAILURE_RE = re.compile(
    r"^\s*(?:\[ERROR\]\s*)?(\w+(?:\[[^\]]*\])?\([^)]*\))\s+.*<<<\s*(?:FAILURE|ERROR)!",
    re.M,
)
# Gradle：``com.example.Class > methodName FAILED``；JUnit5 方法可带括号与参数化索引（v0.44.0）
#   ``com.example.CalcTest > addBasic() FAILED`` / ``com.example.CalcTest > [1] add(int) FAILED``
_JAVA_GRADLE_FAILURE_RE = re.compile(
    r"^\s*([\w.$]+\s+>\s+(?:\[[^\]]*\]\s*)?[\w$]+(?:\([^)]*\))?)\s+FAILED\b",
    re.M,
)
_JAVA_CONSOLE_FAILURE_RE = re.compile(
    r"^\s*[^:]+:\s*(\w+)\(([^)]*)\)(?:\[\d+\])?\s*$",
    re.M,
)
_JAVA_CONSOLE_METHOD_RE = re.compile(r"methodName\s*=\s*'([^']+)'")
# JUnit Console 嵌套格式：`Class.method(ParameterizedTest)[N]`（如 `com.example.CalcTest.testAdd(int, int)[1]`）
_JAVA_CONSOLE_NESTED_RE = re.compile(
    r"^\s*([\w.$]+)\.(\w+)\(([^)]*)\)(?:\[\d+\])?\s*$",
    re.M,
)

# `<<< ERROR!` 异常块含这些标记时判定为超时（v0.24.0）
_JAVA_TIMEOUT_MARKERS = (
    "TimeoutException",
    "TestTimedOutException",
    "timed out",
)

# Gradle 跳过用例行：`com.example.CalcTest > testSkipped SKIPPED`
# Gradle 跳过用例行（含括号 / 参数化）：``CalcTest > testSkipped SKIPPED``
_JAVA_GRADLE_SKIPPED_RE = re.compile(
    r"^\s*([\w.$]+\s+>\s+(?:\[[^\]]*\]\s*)?[\w$]+(?:\([^)]*\))?)\s+SKIPPED\b",
    re.M,
)


def _gradle_aggregate(text: str) -> tuple[int, int, int] | None:
    """聚合 Gradle 测试输出（支持多模块 reactor 的多个 ``N tests completed`` 行）。

    返回 (总用例数, 总失败数, 总跳过数)；无 Gradle 汇总行时返回 None。
    跳过数优先取行内 ``, K skipped`` 字段；缺失时用 ``Class > method SKIPPED`` 行数兜底。
    """
    text = text or ""
    matches = list(_GRADLE_AGG_RE.finditer(text))
    if not matches:
        return None
    total_n = sum(int(m.group("n")) for m in matches)
    total_fail = sum(int(m.group("fail") or 0) for m in matches)
    explicit = [m for m in matches if m.group("skip") is not None]
    if explicit:
        total_skip = sum(int(m.group("skip")) for m in explicit)
    else:
        total_skip = len(_JAVA_GRADLE_SKIPPED_RE.findall(text))
    return total_n, total_fail, total_skip


def _gradle_summary(agg: tuple[int, int, int]) -> str:
    n, fail, skip = agg
    parts = [f"{n} tests completed"]
    parts.append(f"{fail} failed" if fail else "0 failed")
    if skip:
        parts.append(f"{skip} skipped")
    return ", ".join(parts)


def _extract_java_failures_detailed(text: str) -> list[tuple[str, str]]:
    """从 Maven Surefire / Gradle / JUnit Console 输出中提取失败用例，返回 [(名称, 类型)]。

    类型细分（v0.24.0）：
    - ``failure``：断言失败（``<<< FAILURE!`` / ``FAILED``）
    - ``error``：``<<< ERROR!`` 异常
    - ``timeout``：异常块含超时标记（``TimeoutException`` / ``timed out``）
    """
    text = text or ""
    anchors: list[tuple[str, str, int, int]] = []
    for m in _JAVA_SUREFIRE_FAILURE_RE.finditer(text):
        kind = "failure" if "<<< FAILURE!" in m.group(0) else "error"
        anchors.append((m.group(1), kind, m.start(), m.end()))
    for m in _JAVA_GRADLE_FAILURE_RE.finditer(text):
        anchors.append((m.group(1), "failure", m.start(), m.end()))
    for m in _JAVA_CONSOLE_FAILURE_RE.finditer(text):
        cls = m.group(2).rsplit(".", 1)[-1]
        anchors.append((f"{m.group(1)}({cls})", "failure", m.start(), m.end()))
    for m in _JAVA_CONSOLE_NESTED_RE.finditer(text):
        cls = m.group(1).rsplit(".", 1)[-1]
        anchors.append((f"{m.group(2)}({cls})", "failure", m.start(), m.end()))
    for m in _JAVA_CONSOLE_METHOD_RE.finditer(text):
        anchors.append((m.group(1), "failure", m.start(), m.end()))
    anchors.sort(key=lambda a: a[2])
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, (name, kind, _start, end) in enumerate(anchors):
        block_end = anchors[i + 1][2] if i + 1 < len(anchors) else len(text)
        if any(marker in text[end:block_end] for marker in _JAVA_TIMEOUT_MARKERS):
            kind = "timeout"
        if name and name not in seen:
            seen.add(name)
            out.append((name, kind))
    return out[:50]


def _extract_java_failures(text: str) -> list[str]:
    """兼容入口：仅返回失败用例名称（去重，最多 50 个）。"""
    return [name for name, _ in _extract_java_failures_detailed(text)]


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
        r"^\s*(\./|\.\\)?mvnw(?:\.cmd)?\s+test\b",
        r"^\s*gradle\s+test\b",
        r"^\s*(\./|\.\\)?gradlew(?:\.bat)?\s+test\b",
        r"^\s*java\s+-jar\s+.*junit-platform-console-standalone\.jar\b",
        r"^\s*(\./|\.\\)?testng\b",
        r"^\s*java\b[^\n]*\borg\.testng\.TestNG\b",
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
        """解析 Maven Surefire / Gradle / JUnit Console 风格输出。"""
        text = output or ""
        if exit_code == 0:
            summary = _extract_test_summary(text)
            return True, summary or "所有测试通过"
        summary = _extract_test_summary(text)
        failed = _extract_java_failures_detailed(text)
        suffix = ""
        if failed:
            labels = {"failure": "", "error": "异常", "timeout": "超时"}
            parts = [
                name + (f"（{labels[kind]}）" if labels[kind] else "")
                for name, kind in failed
            ]
            suffix = f"；失败用例: {'、'.join(parts)}（{len(failed)} 个）"
        matches = list(_TEST_RUN_AGG_RE.finditer(text))
        if matches:
            m = matches[-1]  # 最后汇总（Results: 之后）
            failures = int(m.group("fail") or 0)
            errors = int(m.group("err") or 0)
            ok = failures == 0 and errors == 0
            detail = summary or f"Tests run: {m.group('run')}, Failures: {failures}, Errors: {errors}"
            return ok, detail + ("" if ok else suffix)
        m = _TESTNG_AGG_RE.search(text)
        if m:
            failures = int(m.group("fail") or 0)
            cfg_fail = int(m.group("cfg") or 0)
            ok = failures == 0 and cfg_fail == 0
            detail = summary or (
                f"Total tests run: {m.group('run')}, Failures: {failures}, "
                f"Skips: {m.group('skip') or 0}"
            )
            if not ok:
                names = _TESTNG_FAILURE_RE.findall(text)
                # Gradle 下运行 TestNG 时失败行为 ``Class > method FAILED`` 风格（v0.44.0）
                names += [m.group(1) for m in _JAVA_GRADLE_FAILURE_RE.finditer(text)]
                if names:
                    detail += "；失败用例: " + "、".join(dict.fromkeys(names))[:400]
            return ok, detail
        agg = _gradle_aggregate(text)
        if agg:
            failures = agg[1]
            ok = failures == 0 and not failed
            detail = summary or _gradle_summary(agg)
            return ok, detail + ("" if ok else suffix)
        # JUnit Platform Console 通常先打印 successful 再打印 failed 行，
        # 须以 failed 行且计数>0 为准，避免误用首行 successful（v0.44.0）
        console_failed = [
            mm
            for mm in _JUNIT_CONSOLE_AGG_RE.finditer(text)
            if mm.group("status") == "failed" and int(mm.group("n")) > 0
        ]
        if console_failed:
            m = console_failed[-1]
            return False, (summary or m.group(0)) + suffix
        if "BUILD FAILURE" in text or "BUILD FAILED" in text:
            return False, (summary or "BUILD FAILURE") + suffix
        return False, summary or (f"测试失败，退出码 {exit_code}" + suffix)


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
    """提取 Maven Surefire / TestNG / Gradle / JUnit Console 的测试汇总行。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # Surefire：取最后一次出现的 ``Tests run:``（同时允许 Maven ``[INFO]`` 前缀，v0.44.0）
    tests_run = [
        ln
        for ln in lines
        if re.match(r"(?:\[INFO\]\s*)?Tests run:\s*\d+", ln)
    ]
    if tests_run:
        return tests_run[-1][:300]
    # TestNG：``Total tests run: N, Failures: M, Skips: K``（允许 [INFO] 前缀）
    testng = [
        ln
        for ln in lines
        if re.match(r"(?:\[INFO\]\s*)?Total tests run:\s*\d+", ln)
    ]
    if testng:
        return testng[-1][:300]
    # Gradle：`3 tests completed, 1 failed`（多模块 reactor 时聚合汇总）
    agg = _gradle_aggregate(text)
    if agg is not None:
        return _gradle_summary(agg)[:300]
    # JUnit Console：``tests successful`` / ``tests failed``；无失败时优先显示 successful 行（v0.44.0）
    console = [
        ln
        for ln in lines
        if re.search(r"\[\s*\d+\s+tests?\s+(successful|failed)\s*\]", ln, re.IGNORECASE)
    ]
    if console:
        failed_nonzero = [
            ln
            for ln in console
            if re.search(r"tests?\s+failed", ln, re.IGNORECASE)
            and int(re.search(r"\d+", ln).group()) > 0
        ]
        if failed_nonzero:
            return failed_nonzero[-1][:300]
        successful = [ln for ln in console if re.search(r"tests?\s+successful", ln, re.IGNORECASE)]
        if successful:
            return successful[-1][:300]
        return console[-1][:300]
    if "BUILD SUCCESSFUL" in text or "BUILD SUCCESS" in text:
        return "BUILD SUCCESS"
    if "BUILD FAILURE" in text or "BUILD FAILED" in text:
        return "BUILD FAILURE"
    return ""
