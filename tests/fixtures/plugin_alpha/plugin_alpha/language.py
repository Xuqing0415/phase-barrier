"""示例语言适配器：为虚构的 .alpha 语言提供支持（fixture，仅测试用）。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from anti_shortcut.languages import LanguageAdapter

_TEST_RE = re.compile(r"\bCASE\b", re.M)
_EXPECT_RE = re.compile(r"\bEXPECT\b", re.M)


class AlphaLanguageAdapter(LanguageAdapter):
    """.alpha 语言适配器：文件识别 / 语法检查 / 测试统计 / 测试命令识别。"""

    name = "alpha"
    source_file_patterns = ["*.alpha"]
    test_file_patterns = ["*.alpha.test"]
    test_command_patterns = [r"^\s*alpha\s+test\b"]

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        path = Path(path)
        if not path.is_file():
            return False, f"{path.name} 不存在，语法检查不通过"
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return False, f"{path.name} 内容为空，语法检查不通过"
        return True, "ok"

    def analyze_tests(self, path: Path) -> dict[str, Any] | None:
        text = path.read_text(encoding="utf-8")
        tests = [
            {"name": f"CASE #{i + 1}", "assertions": 0, "heuristic": True}
            for i, _ in enumerate(_TEST_RE.finditer(text))
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": len(_EXPECT_RE.findall(text)),
        }