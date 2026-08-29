"""语言适配器抽象基类（v0.3.0 语言适配层）。

适配器负责语言相关的四件事：

- 文件识别：``is_source_file`` / ``is_test_file``
- 语法检查：``check_syntax``
- 测试校验：``analyze_tests``（语言相关解析）+ 共享策略 ``validate_test_collection``
- 测试命令：``identify_test_command`` / ``parse_test_output``
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..config import GateConfig
from ..interceptors import summarize_test_output
from ..paths import path_matches

__all__ = ["LanguageAdapter", "analyze_js_style_tests", "validate_test_collection"]

# 常见 JS/TS 测试声明与断言关键字（启发式）
_JS_TEST_DECL_RE = re.compile(
    r"\b(function\s+)?(test|it|describe)(\.(skip|only|todo|each|concurrent|failing|skipIf|retry))?\s*[:()]",
    re.M,
)
_JS_ASSERT_RE = re.compile(
    r"\b(assert|expect|should\.)\b|\.toBe(?:CloseTo)?\b|\.toEqual\b|\.toStrictEqual\b|"
    r"\.toBeTruthy\b|\.toBeFalsy\b|\.toContain(?:Equal)?\b|\.toHaveLength\b|\.toMatch(?:Object)?\b|"
    r"\.toBeNull\b|\.toBeDefined\b|\.toBeUndefined\b|\.rejects\.|\.resolves\.",
    re.M,
)


def _strip_js_comments_strings(text: str) -> str:
    """移除 JS/TS 注释与字符串字面量，避免把注释 / 日志中的 ``test(`` 误判为测试声明。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":  # 行注释
            j = text.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
            continue
        if ch == "/" and nxt == "*":  # 块注释
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if ch in ("'", '"', "`"):  # 字符串字面量
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    break
                j += 1
            i = j + 1 if j < n else n
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def analyze_js_style_tests(text: str) -> dict[str, Any]:
    """启发式分析 JS/TS 风格测试文本：统计测试声明数与断言关键字总数。

    先剥离注释与字符串字面量，再匹配声明 / 断言关键字，降低把
    ``console.log('test(...)')`` 或注释误判为测试声明的概率；
    不引入 acorn 等解析依赖，后续可在适配器中替换为真实解析器。
    """
    cleaned = _strip_js_comments_strings(text)
    declarations = [m.group(0).strip() for m in _JS_TEST_DECL_RE.finditer(cleaned)]
    assertions_total = len(_JS_ASSERT_RE.findall(cleaned))
    tests = [
        {"name": f"<{i + 1}:{decl[:40]}>", "assertions": 0, "heuristic": True}
        for i, decl in enumerate(declarations)
    ]
    return {
        "test_functions": tests,
        "heuristic": True,
        "assertions_total": assertions_total,
    }


def validate_test_collection(config: GateConfig, parsed: list[dict[str, Any]]) -> tuple[bool, str, dict]:
    """对一组测试文件的统计信息执行共享校验策略（与语言无关）。

    :param parsed: ``analyze_tests`` 的结果列表（每个元素含 ``file`` 键时用于报错定位）
    :return: (是否通过, 失败原因, 附加证据)
    """
    # 适配器级错误（如 jest 不可用）优先返回，避免误报“空壳 / 数量不足”
    for item in parsed:
        if item.get("error"):
            loc = item.get("file", "")
            return False, item["error"], {"file": loc, "error": item["error"]}

    # 非 Python 测试文件（启发式）：要求断言关键字总量不低于阈值，防止空壳文件
    for item in parsed:
        if item.get("heuristic") and item.get("assertions_total", 0) < config.min_test_functions:
            loc = item.get("file", "")
            return False, (
                f"测试文件 {loc} 为启发式校验（非 Python），"
                f"断言关键字不足（{item.get('assertions_total', 0)} < {config.min_test_functions}），疑似空壳测试"
            ), {"file": loc, "assertions_total": item.get("assertions_total", 0)}

    all_tests = [t for item in parsed for t in item.get("test_functions", [])]
    if len(all_tests) < config.min_test_functions:
        return False, (
            f"测试函数数量不足：{len(all_tests)} < {config.min_test_functions}（min_test_functions）"
        ), {"test_count": len(all_tests)}

    if config.require_assert_per_test:
        empty = [t["name"] for t in all_tests if not t.get("heuristic") and t.get("assertions", 0) == 0]
        if empty:
            return False, (
                f"以下测试函数不包含任何断言（assert / pytest.raises），疑似空壳测试: {', '.join(empty)}"
            ), {"empty_tests": empty}
    return True, "", {}


class LanguageAdapter(ABC):
    """语言适配器抽象基类。"""

    name: str = ""
    file_extensions: list[str] = []
    source_file_patterns: list[str] = []
    test_file_patterns: list[str] = []
    test_command_patterns: list[str] = []

    def configure(self, options: dict[str, Any]) -> None:
        """用 ``adapter_options`` 配置适配器；默认无操作，子类可覆盖。"""
        return None

    # ---------- 文件识别 ----------

    def _effective_patterns(
        self,
        adapter_patterns: list[str],
        config_patterns: list[str] | None,
    ) -> list[str]:
        """合并适配器默认模式与配置模式（配置模式只增不减）。

        这样 ``language: javascript`` 无需额外配置即可按 JS 默认模式工作，
        同时项目仍可通过 YAML 追加自定义模式（如 ``*.e2e.ts``）。
        """
        merged = list(adapter_patterns)
        for pattern in config_patterns or []:
            if pattern not in merged:
                merged.append(pattern)
        return merged

    def is_source_file(self, path: str | Path, config: GateConfig | None = None) -> bool:
        patterns = self._effective_patterns(
            self.source_file_patterns,
            config.source_file_patterns if config is not None else None,
        )
        return path_matches(Path(path), patterns) and not self.is_test_file(path, config)

    def is_test_file(self, path: str | Path, config: GateConfig | None = None) -> bool:
        patterns = self._effective_patterns(
            self.test_file_patterns,
            config.test_file_patterns if config is not None else None,
        )
        return path_matches(Path(path), patterns)

    # ---------- 语法检查 ----------

    @abstractmethod
    def check_syntax(self, path: Path) -> tuple[bool, str]:
        """语法检查，返回 (是否通过, 错误信息)。"""

    # ---------- 测试校验 ----------

    def analyze_tests(self, path: Path) -> dict[str, Any] | None:
        """解析测试文件，返回统计信息；无法解析（如语法错误）返回 None。"""
        raise NotImplementedError

    def validate_tests(self, path: Path, config: GateConfig) -> tuple[bool, str, dict]:
        """单个测试文件的有效性校验（共享策略）。"""
        info = self.analyze_tests(path)
        if info is None:
            return False, f"测试文件 {path.name} 存在语法错误，无法通过校验", {}
        ok, msg, extra = validate_test_collection(config, [info])
        return ok, msg, extra

    # ---------- 测试命令识别与输出解析 ----------

    def identify_test_command(self, command: str) -> bool:
        cmd = (command or "").strip()
        if not cmd:
            return False
        return any(re.search(p, cmd, flags=re.IGNORECASE) for p in self.test_command_patterns)

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析测试输出，返回 (是否全部通过, 摘要)。默认：退出码 0 视为通过。"""
        rec = summarize_test_output(output, exit_code)
        return rec["passed"], rec["summary"]
