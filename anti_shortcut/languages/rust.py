"""Rust 语言适配器（v0.5.0）：cargo check / rustc 语法检查 + cargo test 输出解析。

- 文件识别：``src/**/*.rs`` 与 ``*.rs`` 为实现；``tests/**/*.rs`` / ``*_test.rs`` /
  ``src/**/tests.rs`` 为测试。内联 ``#[cfg(test)]`` 单元测试默认随源文件一起
  语法检查（不单独计为测试文件），可用 ``test_file_patterns`` 追加
- 语法检查：有 ``Cargo.toml`` 时运行 ``cargo check --message-format short``
  （不检查 test target，避免阶段 2 测试引用未实现函数导致误拦）；
  无项目时回退 ``rustc --edition 2021 --crate-type lib`` 单文件检查；
  工具缺失时返回明确错误
- 测试统计：``#[test]`` / ``#[tokio::test]`` 属性数 + ``assert!`` / ``assert_eq!`` /
  ``assert_ne!`` 断言关键字（启发式）
- 测试命令：``cargo test`` / ``cargo nextest`` / ``rustc --test``
- 输出解析：``test result: ok`` / ``test result: FAILED``
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import LanguageAdapter

__all__ = ["RustAdapter"]

_RUST_TEST_ATTR_RE = re.compile(r"^\s*#\[\s*(tokio::)?test\s*\]", re.M)
_RUST_ASSERT_RE = re.compile(
    r"\b(assert!|assert_eq!|assert_ne!|assert_matches!|assert_approx_eq!)",
    re.M,
)


def _decode_output(raw: bytes | None) -> str:
    """解码 rustc / cargo 输出：优先 UTF-8，回退 GBK（中文 Windows）/ cp1252。"""
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_rust_errors(output: str) -> list[str]:
    """提取 cargo / rustc 输出的 error 行（去掉行号前缀，保留可读信息）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if re.search(r"\berror(\[|:)", stripped):
            out.append(stripped)
    return out


class RustAdapter(LanguageAdapter):
    """Rust：``cargo check`` / ``rustc`` 语法检查 + ``#[test]`` 启发式测试统计。"""

    name = "rust"
    file_extensions = [".rs"]
    source_file_patterns = ["src/**/*.rs", "*.rs"]
    test_file_patterns = [
        "tests/**/*.rs",
        "**/*_test.rs",
        "src/**/tests.rs",
        "src/**/tests/**/*.rs",
    ]
    test_command_patterns = [
        r"^\s*cargo\s+test\b",
        r"^\s*cargo\s+nextest\b",
        r"^\s*rustc\s+--test\b",
    ]

    # ---------- 语法检查 ----------

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        cargo = shutil.which("cargo")
        cargo_root = self._find_cargo_root(path)
        if cargo_root is not None and cargo:
            proc = subprocess.run(
                [cargo, "check", "--message-format", "short"],
                cwd=str(cargo_root),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                return True, "语法检查通过（cargo check）"
            errors = _extract_rust_errors(proc.stderr or proc.stdout)
            first = errors[0] if errors else (proc.stderr or proc.stdout).strip()
            return False, f"Rust 编译错误: {first[:500]}"
        rustc = shutil.which("rustc")
        if rustc:
            return self._check_rustc_single(rustc, path)
        tool = "cargo" if cargo else "cargo / rustc"
        return False, (
            f"未检测到 Rust 工具链（{tool}），无法对 {path.name} 做语法检查；"
            "请安装 Rust（https://rustup.rs/），或在配置中改用其他语言适配器"
        )

    def _check_rustc_single(self, rustc: str, path: Path) -> tuple[bool, str]:
        try:
            with tempfile.TemporaryDirectory(prefix="phase-barrier-rustc-") as td:
                out = Path(td) / "out.rlib"
                proc = subprocess.run(
                    [rustc, "--edition", "2021", "--crate-type", "lib", "-o", str(out), str(path)],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                )
        except OSError as exc:
            return False, f"无法创建 rustc 临时目录: {exc}"
        if proc.returncode == 0:
            return True, "语法检查通过（rustc 单文件）"
        errors = _extract_rust_errors(proc.stderr or proc.stdout)
        first = errors[0] if errors else (proc.stderr or proc.stdout).strip()
        return False, f"Rust 语法错误: {first[:500]}"

    @staticmethod
    def _find_cargo_root(path: Path) -> Path | None:
        """从文件所在目录向上查找包含 Cargo.toml 的项目根。"""
        cur = path if path.is_absolute() else Path(path).resolve()
        for d in (cur.parent, *cur.parents):
            if (d / "Cargo.toml").is_file():
                return d
        return None

    # ---------- 测试统计（启发式） ----------

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        test_count = len(_RUST_TEST_ATTR_RE.findall(text))
        assertions_total = len(_RUST_ASSERT_RE.findall(text))
        tests = [
            {"name": f"<{i + 1}:#[test]>", "assertions": 0, "heuristic": True}
            for i in range(test_count)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": assertions_total,
        }

    # ---------- 测试输出解析（cargo test） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 cargo test 输出：``test result: ok`` / ``test result: FAILED``。"""
        text = output or ""
        if exit_code == 0:
            m = re.search(r"test result:\s*ok\.\s*[^\n]*", text)
            return True, m.group(0) if m else "所有测试通过（cargo test）"
        m = re.search(r"test result:\s*FAILED[^\n]*", text)
        if m:
            return False, m.group(0)
        if "error[" in text or "error:" in text:
            return False, "cargo test 编译失败"
        return False, f"测试失败，退出码 {exit_code}"