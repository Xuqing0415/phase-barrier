"""C# 语言适配器（v0.11.0）：dotnet build 项目级语法检查 + xUnit / NUnit / MSTest 统计与输出解析。

- 文件识别：``*.cs`` 为实现（排除测试）；``*Tests.cs`` / ``*Test.cs`` / ``**/Tests/**`` 为测试
- 语法检查：向上查找 ``*.csproj`` / ``*.sln`` 项目根，有项目根且 ``dotnet`` 可用时运行
  ``dotnet build <root> --nologo --verbosity quiet``（带指纹缓存）；无项目根 / 无工具时返回明确错误。
  C# 没有单文件独立编译入口，因此必须处于完整 .NET 项目内。
- 测试统计：``[Fact]`` / ``[Theory]`` / ``[Test]`` / ``[TestMethod]`` 属性数 + ``Assert.*`` 断言（启发式）
- 测试命令：``dotnet test`` / ``nunit3-console`` / ``dotnet vstest`` / ``msbuild /t:VSTest``
- 输出解析：``dotnet test`` 汇总行 ``Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3``
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["CSharpAdapter"]

_CS_TEST_ATTR_RE = re.compile(r"^\s*\[\s*(Fact|Theory|Test|TestMethod|TestCase)\b", re.M)
_CS_ASSERT_RE = re.compile(r"\bAssert\.|\bAssertions?\.|\bShould\(\)\.", re.M)

# dotnet test 汇总：``Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3``
_DOTNET_SUMMARY_RE = re.compile(
    r"(Passed!|Failed!)\s+-\s+Failed:\s*(\d+),\s+Passed:\s*(\d+),\s+Skipped:\s*(\d+),\s+Total:\s*(\d+)",
    re.M,
)
_NUNIT_RESULT_RE = re.compile(r"Overall\s+result:\s*(Passed|Failed)", re.M)
_NUNIT_COUNTS_RE = re.compile(
    r"(Passed|Failed|Skipped|Error):\s*(\d+)",
    re.M,
)


def _extract_build_errors(output: str) -> list[str]:
    """提取 dotnet build 输出的编译错误行（``error CSxxxx`` / ``error MSBxxxx``）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if re.search(r"\berror\s+(CS|MSB)\d+", stripped):
            out.append(stripped)
    return out


class CSharpAdapter(LanguageAdapter):
    """C#：``dotnet build`` 项目级语法检查 + 属性注解启发式测试统计。"""

    name = "csharp"
    file_extensions = [".cs"]
    source_file_patterns = ["*.cs", "**/*.cs"]
    test_file_patterns = [
        "*Tests.cs",
        "*Test.cs",
        "**/*Tests.cs",
        "**/*Test.cs",
        "**/Tests/**/*.cs",
        "**/test/**/*.cs",
    ]
    test_command_patterns = [
        r"^\s*dotnet\s+test\b",
        r"^\s*dotnet\s+vstest\b",
        r"^\s*nunit3?-?console\b",
        r"^\s*xunit\b",
        r"^\s*msbuild\b.*(/t:VSTest|/t:Test)\b",
    ]

    def __init__(self) -> None:
        self._project_check_cache: dict[tuple, tuple[bool, str]] = {}

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        root = self._find_project_root(path)
        if root is None:
            return False, (
                f"未找到 .csproj / .sln 项目根，无法对 {path.name} 做语法检查；"
                "C# 无单文件独立编译入口，请确保文件位于完整 .NET 项目内"
                "（或在配置中改用其他语言适配器）"
            )
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            return False, (
                f"未检测到 .NET SDK（dotnet），无法对 {path.name} 做语法检查；"
                "请安装 .NET SDK（https://dotnet.microsoft.com/），或在配置中改用其他语言适配器"
            )
        return self._check_project(root, dotnet)

    @staticmethod
    def _find_project_root(path: Path) -> Path | None:
        """从文件所在目录向上查找包含 *.csproj / *.sln 的项目根。"""
        cur = path if path.is_absolute() else Path(path).resolve()
        for d in (cur.parent, *cur.parents):
            if any(d.glob("*.csproj")) or any(d.glob("*.sln")):
                return d
        return None

    @staticmethod
    def _project_fingerprint(root: Path) -> tuple[int, int]:
        """项目 .cs 文件的 (最新修改时间, 文件数) 指纹，用于缓存失效。"""
        max_mtime = 0
        count = 0
        for p in root.rglob("*.cs"):
            if any(part in (".agent_gate", ".git", "bin", "obj") for part in p.parts):
                continue
            try:
                st = p.stat()
                max_mtime = max(max_mtime, st.st_mtime_ns)
            except OSError:
                continue
            count += 1
        return (max_mtime, count)

    def _check_project(self, root: Path, dotnet: str) -> tuple[bool, str]:
        """运行项目级编译（带指纹缓存），返回 (是否通过, 错误信息)。"""
        key = (str(root), self._project_fingerprint(root))
        if key in self._project_check_cache:
            return self._project_check_cache[key]
        proc = subprocess.run(
            [dotnet, "build", str(root), "--nologo", "--verbosity", "quiet"],
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            result = (True, "语法检查通过（dotnet build）")
        else:
            output = (proc.stderr or "") + (proc.stdout or "")
            errors = _extract_build_errors(output)
            first = errors[0] if errors else output.strip()[:500]
            result = (False, f"C# 编译错误: {first[:500]}")
        self._project_check_cache[key] = result
        return result

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        test_count = len(_CS_TEST_ATTR_RE.findall(text))
        assertions_total = len(_CS_ASSERT_RE.findall(text))
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

    # ---------- 测试输出解析（dotnet test / NUnit） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 dotnet test / NUnit 输出：``Passed! - Failed: F, Passed: P, ...``。"""
        text = output or ""
        if exit_code == 0:
            m = _DOTNET_SUMMARY_RE.search(text)
            if m:
                return True, (
                    f"Passed! - Failed: {m.group(2)}, Passed: {m.group(3)}, "
                    f"Skipped: {m.group(4)}, Total: {m.group(5)}"
                )
            if _NUNIT_RESULT_RE.search(text) and _NUNIT_RESULT_RE.search(text).group(1) == "Passed":
                return True, "NUnit 全部通过"
            return True, "所有测试通过（dotnet test）"
        m = _DOTNET_SUMMARY_RE.search(text)
        if m:
            return False, (
                f"dotnet test 失败：Failed: {m.group(2)}, Passed: {m.group(3)}, "
                f"Skipped: {m.group(4)}, Total: {m.group(5)}"
            )
        m2 = _NUNIT_RESULT_RE.search(text)
        if m2:
            return False, f"NUnit 结果: Overall result: {m2.group(1)}"
        if "Build FAILED" in text or "error CS" in text:
            return False, "dotnet test 编译失败"
        return False, f"测试失败，退出码 {exit_code}"
