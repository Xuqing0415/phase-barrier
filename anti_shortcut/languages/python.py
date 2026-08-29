"""Python 语言适配器（默认）：AST 测试分析 + compile 语法检查。"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_SOURCE_FILE_PATTERNS,
    DEFAULT_TEST_COMMANDS,
    DEFAULT_TEST_FILE_PATTERNS,
)
from .base import LanguageAdapter, analyze_js_style_tests

__all__ = ["PythonAdapter", "PYTHON_SUFFIXES"]

# Python 语法相关文件后缀（AST / compile 只对这些后缀执行）
PYTHON_SUFFIXES = {".py", ".pyw"}


class PythonAdapter(LanguageAdapter):
    """Python：``compile`` 语法检查 + ``ast`` 测试统计。"""

    name = "python"
    file_extensions = [".py", ".pyw"]
    source_file_patterns = list(DEFAULT_SOURCE_FILE_PATTERNS)
    test_file_patterns = list(DEFAULT_TEST_FILE_PATTERNS)
    test_command_patterns = list(DEFAULT_TEST_COMMANDS)

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        if path.suffix.lower() not in PYTHON_SUFFIXES:
            if path.stat().st_size == 0:
                return False, f"实现文件 {path.name} 为空文件，请补充实现内容"
            return True, f"非 Python 文件 {path.name} 跳过 compile 语法检查"
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            return False, f"实现文件 {path.name} 存在语法错误: {exc}"
        return True, "语法检查通过"

    def analyze_tests(self, path: Path) -> dict[str, Any] | None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() not in PYTHON_SUFFIXES:
            return analyze_js_style_tests(text)
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            return None
        tests: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                tests.append({"name": node.name, "assertions": _count_assertions(node)})
        return {"test_functions": tests}


def _count_assertions(node: ast.AST) -> int:
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            count += 1
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "raises" and isinstance(child.func.value, ast.Name):
                if child.func.value.id in ("pytest", "self"):
                    count += 1
    return count
