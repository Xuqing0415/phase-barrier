"""Go 语言适配器（v0.5.0）：gofmt 语法检查 + go test 输出解析。

- 文件识别：``*.go`` 为实现，``*_test.go`` 为测试
- 语法检查：调用 ``gofmt -e``（纯语法解析，不触发模块下载 / Go 遥测），
  工具缺失时返回明确错误
- 测试统计：``func TestXxx(t *testing.T)`` 函数数 + ``t.Error`` / ``t.Fatal`` /
  ``assert`` / ``require`` 断言关键字（启发式）
- 测试命令：``go test`` / ``go vet`` 等
- 输出解析：``ok pkg`` / ``FAIL`` / ``--- FAIL:`` 行

语法检查依赖外部工具（Go 工具链）；缺失时校验失败并提示安装，不会静默放行。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["GoAdapter"]

_GO_TEST_FUNC_RE = re.compile(r"^func\s+(Test[A-Z]\w*)\s*\(\s*t\s+\*testing\.T\s*\)", re.M)
_GO_ASSERT_RE = re.compile(
    r"\b(t\.(Error|Errorf|Fatal|Fatalf|Fail|Failf|FailNow|Run)\b|"
    r"assert\.(Equal|True|False|Nil|NotNil|NoError|Error|Contains|Len|Empty)\b|"
    r"require\.(Equal|True|False|Nil|NotNil|NoError|Error|Contains|Len|Empty)\b)",
    re.M,
)

_GO_SUMMARY_RE = re.compile(r"^(ok|FAIL|PASS)\s+\S+", re.M)


def _decode_output(raw: bytes | None) -> str:
    """解码 gofmt / go test 输出：优先 UTF-8（Linux），回退 GBK（中文 Windows）/ cp1252。"""
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_gofmt_errors(output: str) -> list[str]:
    """提取 gofmt 输出的语法错误行（去掉行号前缀，保留可读信息）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if re.search(r":\s*(expected|missing|illegal|syntax error|unexpected)", stripped):
            out.append(stripped)
    return out


def _extract_go_summary(text: str) -> str:
    """从 go test 输出中提取 ``ok pkg time`` / ``FAIL pkg time`` 摘要行。"""
    for ln in (text or "").splitlines():
        stripped = ln.strip()
        if _GO_SUMMARY_RE.match(stripped):
            return stripped
    return ""


class GoAdapter(LanguageAdapter):
    """Go：``gofmt -e`` 语法检查 + ``func TestXxx`` 启发式测试统计。"""

    name = "go"
    file_extensions = [".go"]
    source_file_patterns = ["*.go", "cmd/**/*.go", "internal/**/*.go", "pkg/**/*.go"]
    test_file_patterns = ["*_test.go", "**/*_test.go"]
    test_command_patterns = [
        r"^\s*go\s+test\b",
        r"^\s*go\s+vet\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        gofmt = self._find_gofmt()
        if gofmt is None:
            return False, (
                f"未检测到 Go 工具链（go / gofmt），无法对 {path.name} 做语法检查；"
                "请安装 Go（https://go.dev/dl/），或在配置中改用其他语言适配器"
            )
        proc = subprocess.run([gofmt, "-e", str(path)], capture_output=True)
        if proc.returncode == 0:
            return True, "语法检查通过（gofmt -e）"
        stderr = _decode_output(proc.stderr) or _decode_output(proc.stdout)
        errors = _extract_gofmt_errors(stderr)
        first = errors[0] if errors else stderr.strip()
        return False, f"Go 语法错误: {first[:500]}"

    @staticmethod
    def _find_gofmt() -> str | None:
        """定位 gofmt：优先 PATH，其次与 go 同目录（Go 发行版自带）。"""
        gofmt = shutil.which("gofmt")
        if gofmt:
            return gofmt
        go = shutil.which("go")
        if go:
            candidate = Path(go).with_name("gofmt" + (".exe" if os.name == "nt" else ""))
            if candidate.is_file():
                return str(candidate)
        return None

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        funcs = _GO_TEST_FUNC_RE.findall(text)
        assertions_total = len(_GO_ASSERT_RE.findall(text))
        tests = [
            {"name": f"<{i + 1}:{name}>", "assertions": 0, "heuristic": True}
            for i, name in enumerate(funcs)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（go test） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 go test 输出：``ok pkg`` / ``FAIL`` / ``--- FAIL:`` 行。"""
        text = output or ""
        if exit_code == 0:
            summary = _extract_go_summary(text)
            return True, summary or "所有测试通过（go test）"
        if re.search(r"^---\s+FAIL:", text, re.M):
            return False, "go test 存在失败用例（--- FAIL:）"
        if re.search(r"^FAIL\b", text, re.M):
            return False, "go test 失败（FAIL）"
        if "no test files" in text or "build failed" in text.lower() or "cannot find package" in text:
            return False, "go test 未能运行：项目缺少可执行测试或编译失败"
        return False, f"测试失败，退出码 {exit_code}"