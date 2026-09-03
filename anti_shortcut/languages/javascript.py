"""JavaScript / TypeScript 语言适配器（v0.5.0 增强）。

- 文件识别：``*.test.js`` / ``*.spec.ts`` / ``__tests__/`` 为测试，
  ``src/**`` 与 ``*.js|ts|jsx|tsx`` 为实现
- 语法检查：JS 用 ``node --check``；TS 优先按项目 ``tsconfig.json`` 整体检查
  （``tsc -p <tsconfig> --noEmit``），无 tsconfig 时回退单文件
  ``tsc --noEmit``，单文件模式下无法解析的模块依赖（TS2307 / TS2688 /
  TS7016）降级为“通过（需完整项目验证）”，真正的语法错误仍会被拒绝
- 测试校验：项目安装 acorn 时用真实解析器统计（``test`` / ``it`` / ``describe``
  声明与 ``expect`` / ``assert`` 断言，支持 ``it.each`` / ``test.skip`` 等修饰符），
  否则回退轻量启发式（剥离注释与字符串字面量后正则匹配）；
  可选 ``jest --listTests --json`` 动态发现模式（``adapter_options.test_discovery: jest``
  或自动探测到项目内 jest 时启用），jest 不可用时返回明确错误
- 测试命令：``npm test`` / ``npx jest`` / ``npx vitest`` / ``npx playwright test`` /
  ``yarn test`` / ``npx tsc --noEmit`` 等
- 输出解析：Jest / Vitest 风格（``Tests: N passed`` / ``Test Files: ...``）与
  Playwright 风格（``N passed`` / ``N failed``），退出码非 0 时优先提取失败摘要

语法检查依赖外部工具（Node.js / tsc）；工具缺失时校验失败并提示安装，不会静默放行。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import LanguageAdapter, analyze_js_style_tests

__all__ = ["JavaScriptAdapter"]

_JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_TS_SUFFIXES = {".ts", ".tsx"}

# Jest / Vitest / Playwright 摘要行（v0.7.0 输出解析）
_JS_SUMMARY_PATTERNS = [
    re.compile(r"Tests?:\s+[^\n]+"),
    re.compile(r"Test Files?:\s+[^\n]+"),
    re.compile(r"Test Suites?:\s+[^\n]+"),
    re.compile(r"All specs passed!?"),
    re.compile(r"\b\d+\s+(?:passed|failed|passing|failing)[^\n]*"),
    re.compile(r"\(\d+\s+(?:passing|failing|pending)\s*\)"),
]


def _extract_js_test_summary(text: str) -> str:
    """从 Jest / Vitest / Playwright 输出中提取最相关的摘要行。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for pattern in _JS_SUMMARY_PATTERNS:
        for ln in reversed(lines):
            if pattern.search(ln):
                return ln
    return ""


# tsc 对“单文件无法解析依赖”的典型报错：这些不算语法错误
_TS_DEPENDENCY_MARKERS = (
    "TS2307",  # Cannot find module 'x' or its corresponding type declarations
    "TS2688",  # Cannot find type definition file for 'x'
    "TS7016",  # Could not find a declaration file for module 'x'
)


def _extract_ts_errors(output: str) -> list[str]:
    """提取 tsc 输出中的 error 行（去掉行号前缀，保留可读信息）。"""
    out = []
    for ln in (output or "").splitlines():
        stripped = ln.strip()
        if re.search(r"\berror\s+TS\d+", stripped):
            out.append(stripped)
    return out


class JavaScriptAdapter(LanguageAdapter):
    """JavaScript / TypeScript：``node --check`` / ``tsc --noEmit`` + 启发式 / jest 测试校验。"""

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
        r"^\s*vitest\b",
        r"^\s*playwright\s+test\b",
        r"^\s*npx\s+tsc\s+--noEmit\b",
        r"^\s*npx\s+cypress\s+run\b",
        r"^\s*(?:yarn|pnpm)\s+cypress\s+run\b",
        r"^\s*(?:\.?/?node_modules/\.bin/)?cypress\s+run\b",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._options: dict[str, Any] = {}
        self._test_discovery = "auto"
        self._jest_files: list[str] | None = None  # jest --listTests 结果缓存

    def configure(self, options: dict[str, Any]) -> None:
        """``adapter_options``：

        - ``test_discovery``：``jest``（强制 jest --listTests）/ ``off``（强制启发式）/
          其他值或缺失（自动探测项目内 jest）
        """
        self._options = dict(options or {})
        self._test_discovery = str(self._options.get("test_discovery", "auto")).lower()
        self._jest_files = None

    # ---------- 语法检查 ----------

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
        proc = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return True, "语法检查通过（node --check）"
        return False, f"JavaScript 语法错误: {(proc.stderr or proc.stdout).strip()[:500]}"

    def _tsc_command(self) -> tuple[list[str] | None, str]:
        """返回 (tsc 命令行前缀, 来源说明)；找不到编译器时返回 (None, 错误信息)。"""
        tsc = shutil.which("tsc")
        if tsc:
            return [tsc], "tsc"
        npx = shutil.which("npx")
        if npx:
            return [npx, "--no-install", "tsc"], "npx tsc"
        return None, (
            "未检测到 TypeScript 编译器（tsc / npx tsc），无法对文件做语法检查；"
            "请安装 typescript 依赖后重试"
        )

    def _find_tsconfig(self, path: Path) -> Path | None:
        """从源文件所在目录向上查找最近的 tsconfig.json（含项目根）。"""
        cur = path if path.is_absolute() else Path(path).resolve()
        for d in (cur.parent, *cur.parents):
            candidate = d / "tsconfig.json"
            if candidate.is_file():
                return candidate
        return None

    def _check_ts(self, path: Path) -> tuple[bool, str]:
        cmd, source = self._tsc_command()
        if cmd is None:
            return False, source
        tsconfig = self._find_tsconfig(path)
        if tsconfig is not None:
            # 项目整体检查：模块解析由 tsconfig 负责
            args = [*cmd, "-p", str(tsconfig), "--noEmit", "--pretty", "false"]
        else:
            # 单文件检查：无法解析跨文件模块依赖
            args = [*cmd, "--noEmit", "--pretty", "false", str(path)]
        proc = subprocess.run(
            args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return True, "语法检查通过（tsc --noEmit）"
        errors = _extract_ts_errors(proc.stderr or proc.stdout)
        if (
            tsconfig is None
            and errors
            and all(
                any(marker in err for marker in _TS_DEPENDENCY_MARKERS) for err in errors
            )
        ):
            return True, (
                "语法检查通过（tsc --noEmit，仅存在未解析的模块依赖，"
                "需在完整项目中验证）"
            )
        first = errors[0] if errors else (proc.stderr or proc.stdout).strip()[:500]
        return False, f"TypeScript 语法错误: {first[:500]}"

    # ---------- 测试统计 ----------

    def _find_project_root(self, path: Path) -> Path | None:
        """从文件所在目录向上查找包含 package.json 或 node_modules 的项目根。"""
        cur = path if path.is_absolute() else Path(path).resolve()
        for d in (cur.parent, *cur.parents):
            if (d / "package.json").is_file() or (d / "node_modules").is_dir():
                return d
        return None

    def _use_jest_discovery(self, path: Path) -> bool:
        mode = self._test_discovery
        if mode == "jest":
            return True
        if mode == "off":
            return False
        root = self._find_project_root(path)
        if root is None:
            return False
        return (
            (root / "node_modules" / "jest").is_dir()
            or (root / "node_modules" / ".bin" / "jest").exists()
            or (root / "node_modules" / ".bin" / "jest.cmd").exists()
        )

    def _jest_list_files(self, root: Path) -> list[str] | None:
        """运行 ``jest --listTests --json`` 返回相对项目根的测试文件列表。

        ``--json`` 输出为 ``{"success": true, "testResults": [{"name": ...}]}``；
        某些 jest 版本输出含 ``undefined`` 或直接按行输出，均做兼容回退。
        ``None`` 表示 jest 不可用（命令失败或缺少 npx）；空列表表示可用但未发现测试。
        """
        if self._jest_files is not None:
            return self._jest_files
        npx = shutil.which("npx")
        if not npx:
            self._jest_files = []
            return None
        proc = subprocess.run(
            [npx, "--no-install", "jest", "--listTests", "--json"],
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            self._jest_files = []
            return None
        names: list[str] = []
        try:
            # jest 的 --json 输出可能含非法的 undefined，先做宽松清理
            data = json.loads(proc.stdout.replace("undefined", "null"))
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("testResults"), list):
            for tr in data["testResults"]:
                if isinstance(tr, dict) and tr.get("name"):
                    names.append(str(tr["name"]))
        else:
            names = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        norm: list[str] = []
        for raw in names:
            if not raw:
                continue
            p = Path(raw)
            if p.is_absolute():
                try:
                    p = p.relative_to(root)
                except ValueError:
                    continue
            norm.append(p.as_posix().lstrip("./"))
        self._jest_files = norm
        return norm

    # ---------- 测试输出解析（Jest / Vitest / Playwright） ----------

    def parse_test_output(self, output: str, exit_code: int | None) -> tuple[bool, str]:
        """解析 Jest / Vitest / Playwright 风格输出。"""
        text = output or ""
        if exit_code == 0:
            summary = _extract_js_test_summary(text)
            return True, summary or "所有测试通过"
        summary = _extract_js_test_summary(text)
        if summary:
            return False, summary[:300]
        if re.search(r"FAIL\s+\S+\.(test|spec)\.", text):
            return False, "存在失败的测试文件（FAIL）"
        if re.search(r"\b(\d+)\s+(?:failed|failing)\b", text, re.IGNORECASE):
            return False, "存在失败的测试用例（failed / failing）"
        if "Cypress" in text and ("failed" in text.lower() or "FAILED" in text):
            return False, "Cypress 测试存在失败（failed）"
        return False, f"测试失败，退出码 {exit_code}"

    def analyze_tests(self, path: Path) -> dict[str, Any]:
        if self._use_jest_discovery(path):
            result = self._jest_analyze(path)
            if result is not None:
                return result
            # jest 不可用或该文件未被 jest 发现：回退单文件解析
        return self._parse_test_file(path)

    def _parse_test_file(self, path: Path) -> dict[str, Any]:
        """单文件测试统计：优先 acorn 真实解析（项目有 acorn 时），否则启发式。"""
        info = self._acorn_analyze(path)
        if info is not None:
            return info
        return analyze_js_style_tests(path.read_text(encoding="utf-8", errors="replace"))

    def _acorn_analyze(self, path: Path) -> dict[str, Any] | None:
        """用项目 acorn 真实解析测试文件；acorn 不可用 / 解析失败时返回 None。"""
        node = shutil.which("node")
        helper = Path(__file__).resolve().parent / "js_count_tests.cjs"
        if not node or not helper.is_file():
            return None
        abs_path = path if path.is_absolute() else path.resolve()
        root = self._find_project_root(path)
        proc = subprocess.run(
            [node, str(helper), str(abs_path)],
            cwd=str(root) if root is not None else str(abs_path.parent),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return None
        if not data.get("acorn"):
            return None
        file_info = (data.get("files") or [{}])[0]
        if not file_info or file_info.get("error"):
            return None  # 解析失败（如 TS 语法）-> 回退启发式
        declarations = int(file_info.get("declarations", 0))
        tests = [
            {"name": f"<{i + 1}:acorn>", "assertions": 0, "heuristic": True}
            for i in range(declarations)
        ]
        return {
            "file": str(path),
            "test_functions": tests,
            "heuristic": True,
            "parser": "acorn",
            "assertions_total": int(file_info.get("assertions", 0)),
            "test_cases": int(file_info.get("test_cases", 0)),
            "suites": int(file_info.get("suites", 0)),
        }

    def _jest_analyze(self, path: Path) -> dict[str, Any] | None:
        root = self._find_project_root(path)
        if root is None:
            return None
        files = self._jest_list_files(root)
        if files is None:
            return {
                "file": str(path),
                "test_functions": [],
                "heuristic": True,
                "dynamic": True,
                "assertions_total": 0,
                "error": (
                    f"已启用 jest --listTests 动态发现，但未找到 jest（node_modules/.bin/jest）："
                    "请先安装依赖（npm install），或将 adapter_options.test_discovery 设为 off 改用启发式校验"
                ),
            }
        rel = path.resolve().relative_to(root).as_posix()
        if rel in files or path.name in files:
            parsed = self._parse_test_file(path)
            # 优先用真实解析 / 启发式统计出的测试声明数（更贴近真实用例数），
            # 无法识别声明时回退为“该文件被 jest 发现”的合成计数
            tests = parsed["test_functions"] or [
                {
                    "name": f"<jest:{path.name}>",
                    "assertions": parsed["assertions_total"],
                    "dynamic": True,
                }
            ]
            return {
                "file": str(path),
                "test_functions": tests,
                "heuristic": True,
                "parser": parsed.get("parser"),
                "dynamic": True,
                "assertions_total": parsed["assertions_total"],
                "jest_discovered": len(files),
            }
        return None
