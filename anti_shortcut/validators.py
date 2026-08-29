"""证据校验器：每个阶段对应的自动校验函数。

约定：``validate_xxx(workspace, config, state, adapter=None) -> (ok, message, evidence)``
- ``ok``：是否通过校验
- ``message``：人类可读的通过 / 失败原因（失败时作为拒绝提示返回给 Agent）
- ``evidence``：校验过程中收集的证据（文件哈希、统计信息等），写入状态机
- ``adapter``：语言适配器（v0.3.0）；为 ``None`` 时按 ``get_adapter`` 自动选择

v0.3.0 起，语言相关逻辑（文件识别、语法检查、测试统计）统一由
:class:`anti_shortcut.languages.base.LanguageAdapter` 完成，本模块只保留
与语言无关的阶段校验流程。旧有的 ``analyze_test_file`` / ``classify_path`` /
``path_matches`` 等入口保留为向后兼容的别名。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import GateConfig
from .languages import LanguageAdapter, get_adapter, validate_test_collection
from .languages.python import PYTHON_SUFFIXES, PythonAdapter
from .paths import classify_path, iter_workspace_files, path_matches, sha256_file

__all__ = [
    "PYTHON_SUFFIXES",
    "analyze_test_file",
    "classify_path",
    "iter_workspace_files",
    "path_matches",
    "sha256_file",
    "validate_spec",
    "validate_tests",
    "validate_implementation",
    "validate_test_run",
    "validate_retest",
]


def analyze_test_file(path: Path) -> dict[str, Any] | None:
    """分析测试文件：Python 用 AST（函数数 + 断言），其他语言用轻量启发式。

    向后兼容入口：等价于默认 ``PythonAdapter`` 的 ``analyze_tests``。
    """
    return PythonAdapter().analyze_tests(Path(path))


def _resolve_adapter(
    config: GateConfig,
    workspace: Path,
    adapter: LanguageAdapter | None,
) -> LanguageAdapter:
    """未显式传入适配器时，按配置与工作区自动选择。"""
    return adapter or get_adapter(config, workspace)


# ---------- 阶段 1：Spec 设计 ----------

def validate_spec(
    workspace: Path,
    config: GateConfig,
    state,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict]:
    """校验 spec.md：文件存在 + 必需章节 + 内容长度（与语言无关）。"""
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

def validate_tests(
    workspace: Path,
    config: GateConfig,
    state,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict]:
    """校验测试用例：存在测试文件 + 通过适配器统计测试函数与断言。"""
    adapter = _resolve_adapter(config, workspace, adapter)
    test_files = [
        p for p in iter_workspace_files(workspace, config)
        if adapter.is_test_file(p, config)
    ]
    if not test_files:
        return False, "未找到测试文件（如 test_*.py），请先编写测试用例", {}

    parsed: list[dict[str, Any]] = []
    for tf in test_files:
        info = adapter.analyze_tests(tf)
        if info is None:
            return False, f"测试文件 {tf.name} 存在语法错误，无法通过校验", {}
        parsed.append({"file": str(tf.relative_to(workspace)), **info})

    ok, msg, extra = validate_test_collection(config, parsed)
    if not ok:
        return False, msg, extra

    all_tests = [t for item in parsed for t in item.get("test_functions", [])]
    parsers = sorted({item.get("parser") for item in parsed if item.get("parser")})
    evidence = {
        "files": [item["file"] for item in parsed],
        "sha256": {item["file"]: sha256_file(workspace / item["file"]) for item in parsed},
        "test_functions": [t["name"] for t in all_tests],
        "test_count": len(all_tests),
        "parsers": parsers,
    }
    return True, f"测试用例校验通过（{len(parsed)} 个文件，{len(all_tests)} 个测试函数）", evidence


# ---------- 阶段 3：实现代码 ----------

def validate_implementation(
    workspace: Path,
    config: GateConfig,
    state,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict]:
    """校验实现代码：存在源文件 + 通过适配器做语法检查。"""
    adapter = _resolve_adapter(config, workspace, adapter)
    source_files = [
        p for p in iter_workspace_files(workspace, config)
        if adapter.is_source_file(p, config)
    ]
    if config.require_implementation and not source_files:
        return False, "未找到实现代码文件（非测试的 *.py），请先编写实现", {}

    for sf in source_files:
        ok, msg = adapter.check_syntax(sf)
        if not ok:
            return False, msg, {}

    evidence = {
        "files": [str(sf.relative_to(workspace)) for sf in source_files],
        "sha256": {str(sf.relative_to(workspace)): sha256_file(sf) for sf in source_files},
    }
    return True, f"实现代码校验通过（{len(source_files)} 个文件，语法检查 OK）", evidence


def _check_coverage(config: GateConfig, tr: dict) -> tuple[bool, str]:
    """覆盖率门禁：``config.coverage_threshold`` 配置后，要求测试记录含覆盖率且达标。"""
    threshold = config.coverage_threshold
    if threshold is None:
        return True, ""
    cov = tr.get("coverage")
    if cov is None:
        return False, (
            f"配置了覆盖率门禁（coverage_threshold={threshold}%），"
            "但测试输出中未检测到覆盖率报告（pytest-cov / go test -cover / jest --coverage）"
        )
    if float(cov) < threshold:
        return False, f"覆盖率不足：{cov}% < {threshold}%（coverage_threshold）"
    return True, ""


# ---------- 阶段 4：运行测试 ----------

def validate_test_run(
    workspace: Path,
    config: GateConfig,
    state,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict]:
    """校验测试运行记录是否存在（结果判定在 advance_stage 中处理）。"""
    tr = state.get_evidence("last_test_run") or {}
    if not tr or "exit_code" not in tr:
        return False, "未检测到测试运行记录：请先运行测试命令（如 pytest）", {}
    outcome = "通过" if tr.get("passed") else "未通过"
    cov_ok, cov_msg = _check_coverage(config, tr)
    if not cov_ok:
        return False, cov_msg, {**tr, "coverage": tr.get("coverage")}
    return True, f"测试运行记录存在（exit_code={tr.get('exit_code')}，结果：{outcome}）", tr


# ---------- 阶段 5：修复与回归 ----------

def validate_retest(
    workspace: Path,
    config: GateConfig,
    state,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict]:
    """校验回归：最近一次测试全部通过，且没有“测试后改码未重测”。"""
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

    cov_ok, cov_msg = _check_coverage(config, tr)
    if not cov_ok:
        return False, cov_msg, {**tr, "after_last_change": True, "coverage": tr.get("coverage")}
    return True, "回归测试全部通过", {**tr, "after_last_change": True, "coverage": tr.get("coverage")}
