"""Swift 语言适配器（v0.37.0）：swiftc 语法检查 + XCTest / swift-testing 启发式测试统计。

- 文件识别：``*.swift`` / ``Sources/**`` 为实现（``Package.swift`` 清单除外）；
  测试文件为 ``*Test.swift`` / ``*Tests.swift`` / ``Tests/**`` / ``test/**`` / ``spec/**``
- 语法检查：``swiftc -typecheck <file>`` 单文件检查；脚本模式对含 ``@main`` / 顶层
  可执行代码的文件报错时自动以 ``-parse-as-library`` 重试；跨文件/依赖缺失
  （cannot find / no such module / cannot build module 等）降级为“通过（需完整项目
  编译验证）”，真正的语法错误会被拒绝；swiftc 缺失返回明确错误，不静默放行
- 测试统计：XCTest ``func testXxx()`` 方法 + swift-testing ``@Test`` 属性（启发式），
  断言关键字计数（``XCTAssert*`` / ``XCTFail`` / ``#expect`` / ``#require``）
- 测试命令：``swift test`` / ``xcodebuild test`` / ``xcrun xctest``
- 输出解析：XCTest（``Executed N tests, with M failures`` / ``Test Case ... failed``）、
  swift-testing（``Test run with N tests passed|failed``）与 xcodebuild
  （``** TEST SUCCEEDED **`` / ``** TEST FAILED **``）

语法检查依赖外部工具（swiftc，macOS Xcode CLT / Linux swift.org 工具链）；
缺失时校验失败并提示安装，不会静默放行。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["SwiftAdapter"]

# XCTest：``func testXxx(...)``（行首可选访问修饰 / static / override 等前缀）
_XCTEST_METHOD_RE = re.compile(r"^\s*(?:\w+\s+)*func\s+(test\w*)\s*\(", re.M)
# swift-testing：``@Test`` 属性（可带 ``@Test(arguments:)`` / traits 等参数）
_SWIFT_TESTING_ATTRIBUTE_RE = re.compile(r"^\s*@Test\b", re.M)
# XCTest / swift-testing 断言关键字（启发式）
_SWIFT_ASSERT_RE = re.compile(
    r"\bXCTAssert\w*\s*\(|\bXCTFail\s*\(|(?<!\w)#expect\s*\(|(?<!\w)#require\s*\(",
    re.M,
)

# swiftc 对“单文件无法解析跨文件/库符号”的典型报错：仅依赖缺失不算语法错误
_SWIFT_DEPENDENCY_MARKERS = (
    "cannot find",  # cannot find 'x' in scope / cannot find type
    "cannot build module",
    "cannot load",
    "no such module",
    "missing required module",
    "unresolved",
)

# XCTest 汇总：``Executed N tests, with M failures (U unexpected)``（多套件时取最后一次）
_XCTEST_EXECUTED_RE = re.compile(
    r"Executed\s+(?P<n>\d+)\s+tests?(?:,\s+with\s+(?P<fail>\d+)\s+failures?)?",
    re.M,
)
# swift-testing 汇总：``Test run with N tests passed|failed``
_SWIFT_TESTING_RUN_RE = re.compile(
    r"Test run with\s+(?P<n>\d+)\s+tests?\s+(?P<status>passed|failed)\b",
    re.M,
)
_XCTEST_FAILED_CASE_RE = re.compile(r"Test Case\s+'[^']*'\s+failed\b", re.M)



def _strip_swift_comments_strings(text: str) -> str:
    """移除 Swift 注释与字符串字面量，避免把注释 / 字符串中的 test / 断言误判。

    覆盖：``//`` 行注释、``/* */``（支持嵌套）块注释、双引号字符串与
    三个双引号的多行字符串；注释 / 字符串统一替换为空格，保持行列结构近似稳定。
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
            while j < n and depth:
                if text[j:j + 2] == "/*":
                    depth += 1
                    j += 2
                elif text[j:j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            i = n if j > n else j
            out.append(" ")
            continue
        if ch == '"':
            if text[i:i + 3] == '"""':
                j = i + 3
                while j < n:
                    if text[j:j + 3] == '"""':
                        j += 3
                        break
                    if text[j] == "\\":
                        j += 2
                        continue
                    j += 1
                i = n if j > n else j
            else:
                j = i + 1
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == '"':
                        j += 1
                        break
                    j += 1
                i = n if j > n else j
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _decode_output(raw: bytes | None) -> str:
    """解码 swiftc 输出：优先 UTF-8（Linux），回退 GBK（中文 Windows）/ cp1252。"""
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _swiftc_errors(text: str) -> list[str]:
    """提取 swiftc 报错行（含 ``error:`` 关键字的行，去掉行号前缀保留可读信息）。"""
    return [ln.strip() for ln in text.splitlines() if "error:" in ln.lower()]


def _run_swiftc(swiftc: str, path: Path, *flags: str) -> "subprocess.CompletedProcess[bytes]":
    """执行 ``swiftc [flags] -typecheck <file>``（flags 置于 -typecheck 之前）。"""
    return subprocess.run([swiftc, *flags, "-typecheck", str(path)], capture_output=True)


def _xctest_aggregate(text: str) -> tuple[int, int] | None:
    """聚合 XCTest 输出：返回最后一次 ``Executed N tests, with M failures``。

    每个 Test Suite 会各自打印一条 ``Executed`` 汇总，总套件（All tests）位于末尾，
    因此取最后一次出现。
    """
    matches = list(_XCTEST_EXECUTED_RE.finditer(text or ""))
    if not matches:
        return None
    m = matches[-1]
    return int(m.group("n")), int(m.group("fail") or 0)


def _swift_testing_run(text: str) -> tuple[int, str] | None:
    """解析 swift-testing 汇总：``Test run with N tests passed|failed``。"""
    m = _SWIFT_TESTING_RUN_RE.search(text or "")
    if not m:
        return None
    return int(m.group("n")), m.group("status")


class SwiftAdapter(LanguageAdapter):
    """Swift：swiftc 语法检查 + XCTest / swift-testing 启发式测试统计。"""

    name = "swift"
    file_extensions = [".swift"]
    source_file_patterns = [
        "Sources/**/*.swift",
        "**/*.swift",
        "*.swift",
    ]
    test_file_patterns = [
        "*Test.swift",
        "*Tests.swift",
        "**/*Test.swift",
        "**/*Tests.swift",
        "Tests/**/*.swift",
        "**/Tests/**/*.swift",
        "test/**/*.swift",
        "spec/**/*.swift",
    ]
    test_command_patterns = [
        r"^\s*(\./|\.\\)?swift\s+test\b",
        r"^\s*xcodebuild\b[^\n]*\btest\b",
        r"^\s*xcrun\s+xctest\b",
        r"^\s*xctest\b",
    ]

    # ---------- 文件识别 ----------

    def is_source_file(self, path, config=None) -> bool:
        # SwiftPM 清单不是实现文件（swiftc -typecheck 会因 package API 上下文报错）
        if Path(path).name == "Package.swift":
            return False
        return super().is_source_file(path, config)

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        swiftc = shutil.which("swiftc")
        if not swiftc:
            return False, (
                f"未检测到 Swift 编译器（swiftc），无法对 {path.name} 做语法检查；"
                "macOS 请先安装 Xcode 命令行工具（xcode-select --install），"
                "Linux 请安装 swift.org 工具链，或在配置中显式指定其他语言适配器"
            )
        proc = _run_swiftc(swiftc, path)
        if proc.returncode == 0:
            return True, "语法检查通过（swiftc -typecheck）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _swiftc_errors(stderr)
        # 脚本模式不允许 @main / 顶层可执行代码专属写法：以库模式（-parse-as-library）重试
        if errors and any(
            "main attribute" in err or "top-level" in err for err in errors
        ):
            retry = _run_swiftc(swiftc, path, "-parse-as-library")
            if retry.returncode == 0:
                return True, "语法检查通过（swiftc -typecheck -parse-as-library）"
            stderr = _decode_output(retry.stderr) or _decode_output(retry.stdout)
            errors = _swiftc_errors(stderr)
        if errors and all(
            any(marker in err for marker in _SWIFT_DEPENDENCY_MARKERS) for err in errors
        ):
            return True, (
                "语法检查通过（swiftc，仅存在未解析的跨文件/依赖引用，"
                "需在完整项目中编译验证）"
            )
        first = errors[0] if errors else stderr.strip()
        return False, f"Swift 语法错误: {first[:500]}"

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_swift_comments_strings(text)
        xctest_names = [m.group(1) for m in _XCTEST_METHOD_RE.finditer(cleaned)]
        swift_testing_count = len(_SWIFT_TESTING_ATTRIBUTE_RE.findall(cleaned))
        assertions_total = len(_SWIFT_ASSERT_RE.findall(cleaned))
        tests = [
            {"name": f"<{i + 1}:{name}>", "assertions": 0, "heuristic": True}
            for i, name in enumerate(xctest_names)
        ]
        base = len(tests)
        tests.extend(
            {"name": f"<{base + i + 1}:@Test>", "assertions": 0, "heuristic": True}
            for i in range(swift_testing_count)
        )
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（XCTest / swift-testing / xcodebuild，其余回退通用） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        text = output or ""
        agg = _xctest_aggregate(text)
        if agg is not None:
            n, failed = agg
            ok = failed == 0 and exit_code in (None, 0)
            return ok, f"XCTest: Executed {n} tests, with {failed} failures"
        run = _swift_testing_run(text)
        if run is not None:
            n, status = run
            ok = status == "passed" and exit_code in (None, 0)
            return ok, f"swift-testing: Test run with {n} tests {status}"
        if _XCTEST_FAILED_CASE_RE.search(text):
            return False, "XCTest 有测试失败"
        if "** TEST FAILED **" in text:
            return False, "xcodebuild 测试失败"
        if "** TEST SUCCEEDED **" in text and exit_code in (None, 0):
            return True, "xcodebuild 测试通过"
        if not text and exit_code not in (None, 0):
            return False, f"退出码 {exit_code}"
        return super().parse_test_output(text, exit_code)
