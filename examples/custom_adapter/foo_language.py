"""自定义语言适配器示例：为虚构的 .foo 语言提供支持。

对照 README「自定义适配器」章节：实现 ``LanguageAdapter`` 接口后，
即可通过配置导入路径（``language_adapter``）或入口点（``phase_barrier.languages``）加载。

约定（演示用）：
- ``*.foo``          -> 实现文件
- ``*.test.foo``     -> 测试文件
- 测试文件内用 ``TEST`` 声明用例、``ASSERT`` 写断言
- 测试命令为 ``foo test``

运行示例：
    python examples/custom_adapter/demo.py
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from anti_shortcut.languages import LanguageAdapter

_FOO_TEST_RE = re.compile(r"\bTEST\b", re.M)
_FOO_ASSERT_RE = re.compile(r"\bASSERT\b", re.M)


class FooAdapter(LanguageAdapter):
    """.foo 语言适配器：文件识别 / 语法检查 / 测试统计 / 测试命令识别。"""

    name = "foo"
    source_file_patterns = ["*.foo"]
    test_file_patterns = ["*.test.foo"]
    test_command_patterns = [r"^\s*foo\s+test\b"]

    def check_syntax(self, path: Path) -> tuple[bool, str]:
        """语法检查：真实场景可调用对应编译器；这里演示接口约定（非空即可）。"""
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return False, f"{path.name} 内容为空，语法检查不通过"
        return True, "ok"

    def analyze_tests(self, path: Path) -> dict[str, Any] | None:
        """启发式统计 TEST 声明与 ASSERT 断言（可参考 JavaScriptAdapter 的实现）。"""
        text = path.read_text(encoding="utf-8")
        tests = [
            {"name": f"TEST #{i + 1}", "assertions": 0, "heuristic": True}
            for i, _ in enumerate(_FOO_TEST_RE.finditer(text))
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "assertions_total": len(_FOO_ASSERT_RE.findall(text)),
        }