"""证据校验器：每个阶段对应的自动校验函数。

约定：``validate_xxx(workspace, config, state) -> (ok: bool, message: str, evidence: dict)``
- ``ok``：是否通过校验
- ``message``：人类可读的通过 / 失败原因（失败时作为拒绝提示返回给 Agent）
- ``evidence``：校验过程中收集的证据（文件哈希、统计信息等），写入状态机
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from .config import GateConfig


# Python 语法相关文件后缀（AST / compile 只对这些后缀执行）
PYTHON_SUFFIXES = {".py", ".pyw"}

# 非 Python（如 JS/TS）测试文件的轻量启发式：识别常见测试声明与断言关键字
_NON_PY_TEST_DECL_RE = re.compile(r"\b(function\s+)?(test|it|describe)\s*[:()]", re.M)
_NON_PY_ASSERT_RE = re.compile(r"\b(assert|expect|should\.)\b|\.toBe\b|\.toEqual\b|\.toStrictEqual\b", re.M)


def _analyze_non_python_test(text: str) -> dict[str, Any]:
    """启发式分析非 Python 测试文件：按常见 JS/TS 声明统计测试与断言。

    完整的多语言适配（acorn / tsc 解析）在 Roadmap 的 v0.3.0 中规划；
    这里提供“非空壳”级别的检查：测试声明数 + 断言关键字总数。
    """
    declarations = [m.group(0).strip() for m in _NON_PY_TEST_DECL_RE.finditer(text)]
    assertions_total = len(_NON_PY_ASSERT_RE.findall(text))
    tests = [
        {"name": f"<{i + 1}:{decl[:40]}>", "assertions": 0, "heuristic": True}
        for i, decl in enumerate(declarations)
    ]
    return {
        "test_functions": tests,
        "heuristic": True,
        "assertions_total": assertions_total,
    }


# ---------- 通用工具 ----------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _glob_part_to_regex(part: str) -> str:
    """把 glob 片段转换为正则（* 不跨目录，? 匹配单字符）。"""
    out: list[str] = []
    for ch in part:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _pattern_to_regex(pattern: str) -> re.Pattern:
    """把 glob 模式转换为正则，支持 ``**``（零个或多个目录层级）。"""
    parts = pattern.replace("\\", "/").split("/")
    out: list[str] = []
    prev_dstar = False
    n = len(parts)
    for i, part in enumerate(parts):
        if part == "":
            continue
        if part == "**":
            if out:
                out.append("/")
            out.append("(?:[^/]+(?:/[^/]+)*/)?" if i < n - 1 else "(?:[^/]+(?:/[^/]+)*)?")
            prev_dstar = True
        else:
            if out and not prev_dstar:
                out.append("/")
            out.append(_glob_part_to_regex(part))
            prev_dstar = False
    return re.compile("^" + "".join(out) + "$")


def path_matches(path: Path, patterns: list[str]) -> bool:
    """判断路径是否匹配任意 glob 模式（支持 ``**`` 递归目录）。

    匹配候选包括：完整路径、文件名、以及路径的每个尾缀（目录级模式在
    绝对路径 / 相对路径下都能命中，例如 ``src/**/*.ts`` 可匹配
    ``D:/ws/src/fib.ts`` 的尾缀 ``src/fib.ts``）。
    """
    posix = path.as_posix()
    parts = posix.split("/")
    candidates = {posix, path.name, *( "/".join(parts[i:]) for i in range(len(parts)) )}
    for pattern in patterns:
        regex = _pattern_to_regex(pattern)
        for candidate in candidates:
            if regex.match(candidate):
                return True
    return False


def classify_path(path: str | Path, config: GateConfig) -> str:
    """把路径分类为 test / source / other。"""
    p = Path(path)
    if path_matches(p, config.test_file_patterns):
        return "test"
    if path_matches(p, config.source_file_patterns):
        return "source"
    return "other"


def iter_workspace_files(workspace: Path, config: GateConfig) -> list[Path]:
    """遍历工作区文件，跳过门禁目录与常见无关目录。"""
    skip = {config.gate_dir_name, ".git", "__pycache__", ".venv", "venv", "node_modules"}
    out: list[Path] = []
    for p in workspace.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(workspace).parts
        if any(part in skip for part in rel_parts):
            continue
        out.append(p)
    return sorted(out)


def analyze_test_file(path: Path) -> dict[str, Any] | None:
    """分析测试文件：Python 用 AST（函数数 + 断言），其他语言用轻量启发式。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() not in PYTHON_SUFFIXES:
        return _analyze_non_python_test(text)
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


# ---------- 阶段 1：Spec 设计 ----------

def validate_spec(workspace: Path, config: GateConfig, state) -> tuple[bool, str, dict]:
    spec_path = workspace / config.spec_file
    if not spec_path.exists():
        return False, f"缺少 spec 文件：{config.spec_file}（请先完成 Spec 设计）", {}
    content = spec_path.read_text(encoding="utf-8", errors="replace")
    missing = [s for s in config.spec_sections if s not in content]
    if missing:
        return False, f"spec 缺少必需章节: {', '.join(missing)}", {"file": str(spec_path)}
    if len(content) < config.spec_min_chars:
        return False, (
            f"spec 内容过于简略（{len(content)} 字符 < {config.spec_min_chars}），"
            f"请补充需求分析、设计方案与接口定义的具体内容"
        ), {"file": str(spec_path), "chars": len(content)}
    evidence = {
        "file": str(spec_path.relative_to(workspace)),
        "sha256": sha256_file(spec_path),
        "chars": len(content),
        "sections_found": config.spec_sections,
    }
    return True, "spec 校验通过", evidence


# ---------- 阶段 2：测试用例编写 ----------

def validate_tests(workspace: Path, config: GateConfig, state) -> tuple[bool, str, dict]:
    test_files = [
        p for p in iter_workspace_files(workspace, config)
        if path_matches(p, config.test_file_patterns)
    ]
    if not test_files:
        return False, "未找到测试文件（如 test_*.py），请先编写测试用例", {}

    parsed: list[dict[str, Any]] = []
    for tf in test_files:
        info = analyze_test_file(tf)
        if info is None:
            return False, f"测试文件 {tf.name} 存在语法错误，无法通过校验", {}
        parsed.append({"file": str(tf.relative_to(workspace)), **info})

    # 非 Python 测试文件（启发式）：要求断言关键字总量不低于阈值，防止空壳文件
    for item in parsed:
        if item.get("heuristic") and item["assertions_total"] < config.min_test_functions:
            return False, (
                f"测试文件 {item['file']} 为启发式校验（非 Python），"
                f"断言关键字不足（{item['assertions_total']} < {config.min_test_functions}），疑似空壳测试"
            ), {"file": item["file"], "assertions_total": item["assertions_total"]}

    all_tests = [t for item in parsed for t in item["test_functions"]]
    if len(all_tests) < config.min_test_functions:
        return False, (
            f"测试函数数量不足：{len(all_tests)} < {config.min_test_functions}（min_test_functions）"
        ), {"test_count": len(all_tests)}

    if config.require_assert_per_test:
        empty = [t["name"] for t in all_tests if not t.get("heuristic") and t["assertions"] == 0]
        if empty:
            return False, (
                f"以下测试函数不包含任何断言（assert / pytest.raises），疑似空壳测试: {', '.join(empty)}"
            ), {"empty_tests": empty}

    evidence = {
        "files": [item["file"] for item in parsed],
        "sha256": {item["file"]: sha256_file(workspace / item["file"]) for item in parsed},
        "test_functions": [t["name"] for t in all_tests],
        "test_count": len(all_tests),
    }
    return True, f"测试用例校验通过（{len(parsed)} 个文件，{len(all_tests)} 个测试函数）", evidence


# ---------- 阶段 3：实现代码 ----------

def validate_implementation(workspace: Path, config: GateConfig, state) -> tuple[bool, str, dict]:
    source_files = [
        p for p in iter_workspace_files(workspace, config)
        if path_matches(p, config.source_file_patterns)
        and not path_matches(p, config.test_file_patterns)
    ]
    if config.require_implementation and not source_files:
        return False, "未找到实现代码文件（非测试的 *.py），请先编写实现", {}

    for sf in source_files:
        if sf.suffix.lower() in PYTHON_SUFFIXES:
            text = sf.read_text(encoding="utf-8", errors="replace")
            try:
                compile(text, str(sf), "exec")
            except SyntaxError as exc:
                return False, f"实现文件 {sf.name} 存在语法错误: {exc}", {}
        elif sf.stat().st_size == 0:
            return False, f"实现文件 {sf.name} 为空文件，请补充实现内容", {}

    evidence = {
        "files": [str(sf.relative_to(workspace)) for sf in source_files],
        "sha256": {str(sf.relative_to(workspace)): sha256_file(sf) for sf in source_files},
    }
    return True, f"实现代码校验通过（{len(source_files)} 个文件，语法检查 OK）", evidence


# ---------- 阶段 4：运行测试 ----------

def validate_test_run(workspace: Path, config: GateConfig, state) -> tuple[bool, str, dict]:
    tr = state.get_evidence("last_test_run") or {}
    if not tr or "exit_code" not in tr:
        return False, "未检测到测试运行记录：请先运行测试命令（如 pytest）", {}
    outcome = "通过" if tr.get("passed") else "未通过"
    return True, f"测试运行记录存在（exit_code={tr.get('exit_code')}，结果：{outcome}）", tr


# ---------- 阶段 5：修复与回归 ----------

def validate_retest(workspace: Path, config: GateConfig, state) -> tuple[bool, str, dict]:
    tr = state.get_evidence("last_test_run") or {}
    if not tr or not tr.get("passed"):
        reason = "未检测到测试运行记录" if not tr else f"最近一次测试未通过（exit_code={tr.get('exit_code')}）"
        return False, f"{reason}：请先修复代码并重新运行测试，直到全部通过", tr

    changed_at = state.get_evidence("last_source_change_at_epoch")
    ran_at = tr.get("at_epoch")
    if changed_at is not None and (ran_at is None or ran_at < changed_at):
        return False, (
            "检测到代码/测试文件在最近一次测试运行之后被修改：请重新运行测试确认回归通过"
        ), {**tr, "after_last_change": False}

    return True, "回归测试全部通过", {**tr, "after_last_change": True}
