"""JavaScript / TypeScript 语言适配器（示例实现）。

语法检查依赖外部工具（Node.js / tsc）；工具缺失时返回明确错误信息，
不会静默失败。核心包不依赖 Node，适配器按需调用。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter, analyze_js_style_tests

__all__ = ["JavaScriptAdapter"]

_JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_TS_SUFFIXES = {".ts", ".tsx"}


class JavaScriptAdapter(LanguageAdapter):
    """JavaScript / TypeScript：``node --check`` / ``tsc --noEmit`` + 正则测试启发式。"""

    name = "javascript"
    file_extensions = sorted(_JS_SUFFIXES)
    source_file_patterns = [
        "src/**/*.ts",
        "src/**/*.js",
        "src/**/*.tsx",
        "src/**/*.jsx",
        "*.ts",
        "*.js",
        "*.tsx",
        "*.jsx",
    ]
    test_file_patterns = [
        "*.test.js",
        "*.spec.js",
        "*.test.ts",
        "*.spec.ts",
        "*.test.jsx",
        "*.test.tsx",
        "*.spec.tsx",
        "tests/**/*.test.js",
        "tests/**/*.test.ts",
        "__tests__/**/*.js",
        "__tests__/**/*.ts",
    ]
    test_command_patterns = [
        r"^\s*npm\s+test\b",
        r"^\s*npx\s+(jest|vitest|mocha|playwright)\b",
        r"^\s*yarn\s+test\b",
        r"^\s*pnpm\s+test\b",
        r"^\s*npx\s+tsc\s+--noEmit\b",
    ]

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.stat().st_size == 0:
            return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
        if path.suffix.lower() in _TS_SUFFIXES:
            return self._check_ts(path)
        return self._check_js(path)

    def _check_js(self, path: Path) -> tuple[bool, str]:
        node = shutil.which("node")
        if not node:
            return False, (
                f"未检测到 Node.js（node），无法对 {path.name} 做语法检查；"
                "请安装 Node.js，或在配置中改用其他语言适配器"
            )
        proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if proc.returncode == 0:
            return True, "语法检查通过（node --check）"
        return False, f"JavaScript 语法错误: {(proc.stderr or proc.stdout).strip()[:500]}"

    def _check_ts(self, path: Path) -> tuple[bool, str]:
        tsc = shutil.which("tsc")
        if tsc:
            proc = subprocess.run(
                [tsc, "--noEmit", "--pretty", "false", str(path)],
                capture_output=True,
                text=True,
            )
        else:
            npx = shutil.which("npx")
            if not npx:
                return False, (
                    f"未检测到 TypeScript 编译器（tsc / npx tsc），无法对 {path.name} 做语法检查；"
                    "请安装 typescript 依赖后重试"
                )
            proc = subprocess.run(
                [npx, "--no-install", "tsc", "--noEmit", "--pretty", "false", str(path)],
                capture_output=True,
                text=True,
            )
        if proc.returncode == 0:
            return True, "语法检查通过（tsc --noEmit）"
        return False, f"TypeScript 语法错误: {(proc.stderr or proc.stdout).strip()[:500]}"

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        return analyze_js_style_tests(path.read_text(encoding="utf-8", errors="replace"))
