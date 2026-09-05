"""语义级校验（v0.49.0）：在结构校验之上提供可扩展的语义增强门禁。

背景
----
内置阶段校验器检查的是“结构 / 形式”证据（章节存在、测试函数、语法、测试退出码、
覆盖率），理论上可以被“形式上完整但内容空泛”的产物绕过：空泛 spec、无断言测试、
永远通过的测试。本模块引入**语义级校验器**接口与两个内置实现，把
“需求 ↔ 测试 ↔ 实现”的关联质量纳入阶段推进门禁：

- ``RequirementCoverageValidator``（需求追踪，v0.49.0）：spec 以 ``REQ-001``
  声明需求条目，测试文件以 ``# REQ-001`` 注释显式关联；推进阶段 2 时检查每个
  需求是否至少被一个测试引用，防“spec 与测试脱节”。
- ``MutationScoreValidator``（变异测试，v0.49.0）：对实现做确定性 AST 变异
  （运算符 / 布尔 / 比较翻转），用工作区现有测试集逐个运行变异体；存活变异体
  越多说明测试越弱，防“空测试 / 假断言”。v0.49.0 仅支持 Python 项目。

第三方（含 LLM 审查）语义校验器通过入口点组
``phase_barrier.semantic_validators`` 注册（与语言适配器 / 阶段校验器 /
拦截规则同模式），契约见 :class:`SemanticValidator` 与
:func:`run_semantic_checks`。

设计边界（v0.49.0）：
- 默认全部关闭（``semantic.*.enabled: false``），不改变既有门禁行为；
- 失败给出可操作的 Agent 提示（缺哪些 REQ / 突变分数多少 / 怎么提高）；
- 语义校验是“增强”而非“替代”结构校验，最终防线仍是人工审查证据包。
"""
from __future__ import annotations

import ast
import importlib.metadata as metadata
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import GateConfig, SemanticOptions
from .languages import LanguageAdapter, get_adapter
from .paths import iter_workspace_files

__all__ = [
    "SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP",
    "BUILTIN_SEMANTIC_VALIDATORS",
    "SemanticValidator",
    "SemanticCheckResult",
    "RequirementCoverageValidator",
    "MutationScoreValidator",
    "register_semantic_validator",
    "load_semantic_plugins",
    "run_semantic_checks",
    "extract_requirement_ids",
    "extract_test_references",
    "generate_mutations",
]

SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP = "phase_barrier.semantic_validators"

_REQ_RE = re.compile(r"\bREQ-(\d{1,6})\b")
# 测试文件内关联标注：注释形式 `# REQ-001`（允许大小写、空白）
_REQ_MARK_RE = re.compile(r"#\s*REQ-(\d{1,6})\b", re.IGNORECASE)


class SemanticCheckResult:
    """单个语义校验器的一次检查结果。"""

    __slots__ = ("ok", "message", "evidence")

    def __init__(self, ok: bool, message: str, evidence: dict[str, Any]) -> None:
        self.ok = ok
        self.message = message
        self.evidence = evidence or {}


class SemanticValidator:
    """语义校验器基类（插件契约）。

    子类需提供：``name``（唯一标识，对应 ``config.semantic.<name>`` 配置节）、
    ``stages``（在哪些“当前阶段”推进时运行）、``check(workspace, config, state,
    adapter=None) -> SemanticCheckResult``。``run_semantic_checks`` 会先检查
    ``config.semantic.<name>.enabled``，关闭时跳过。
    """

    name: str = ""
    description: str = ""
    stages: tuple[int, ...] = ()

    def check(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        adapter: LanguageAdapter | None = None,
    ) -> SemanticCheckResult:
        raise NotImplementedError

    def config_key(self) -> str:
        return self.name


# ---------- 需求追踪：REQ 提取与测试引用 ----------

def extract_requirement_ids(content: str) -> list[str]:
    """从 spec 内容提取 ``REQ-001`` 需求 ID（保序去重，数字归一为原样）。"""
    seen: list[str] = []
    for raw in _REQ_RE.findall(content):
        rid = f"REQ-{int(raw):03d}"
        if rid not in seen:
            seen.append(rid)
    return seen


def extract_test_references(content: str) -> list[str]:
    """从测试文件内容提取 ``# REQ-001`` 关联标注（保序去重）。"""
    seen: list[str] = []
    for raw in _REQ_MARK_RE.findall(content):
        rid = f"REQ-{int(raw):03d}"
        if rid not in seen:
            seen.append(rid)
    return seen


class RequirementCoverageValidator(SemanticValidator):
    """需求追踪校验：spec 的每个 REQ 必须被至少一个测试显式引用。"""

    name = "requirement_coverage"
    description = "spec REQ-xxx 需求条目须被测试文件以 # REQ-xxx 注释覆盖"
    stages = (2,)

    def check(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        adapter: LanguageAdapter | None = None,
    ) -> SemanticCheckResult:
        adapter = adapter or get_adapter(config, workspace)
        spec_path = workspace / config.spec_file
        spec_reqs = (
            extract_requirement_ids(spec_path.read_text(encoding="utf-8", errors="replace"))
            if spec_path.exists()
            else []
        )
        if not spec_reqs:
            return SemanticCheckResult(
                True,
                "需求追踪：spec 未声明 REQ-xx 需求条目，跳过（可在 spec 中写 `REQ-001: ...` 启用）",
                {"requirements": [], "coverage": None, "note": "no_reqs"},
            )

        refs: dict[str, list[str]] = {}
        for tf in iter_workspace_files(workspace, config):
            if not adapter.is_test_file(tf, config):
                continue
            found = extract_test_references(tf.read_text(encoding="utf-8", errors="replace"))
            if found:
                refs[str(tf.relative_to(workspace))] = found

        referenced = [rid for ids in refs.values() for rid in ids]
        covered = [rid for rid in spec_reqs if rid in referenced]
        uncovered = [rid for rid in spec_reqs if rid not in referenced]
        coverage = round(100.0 * len(covered) / len(spec_reqs), 1) if spec_reqs else 100.0
        opts = config.semantic.requirement_coverage
        evidence = {
            "requirements": spec_reqs,
            "test_references": refs,
            "covered": covered,
            "uncovered": uncovered,
            "coverage": coverage,
        }
        if uncovered and coverage < opts.min_coverage:
            return SemanticCheckResult(
                False,
                "需求追踪未通过：以下需求没有任何测试引用："
                + ", ".join(uncovered)
                + f"（覆盖率 {coverage}% < {opts.min_coverage}%）。请在对应测试文件中加注释关联，如 `# REQ-001`",
                evidence,
            )
        return SemanticCheckResult(
            True,
            f"需求追踪通过（{len(covered)}/{len(spec_reqs)} 个需求被测试引用，覆盖率 {coverage}%）",
            evidence,
        )


# ---------- 变异测试：AST 变异体生成 ----------

class _Mutator(ast.NodeTransformer):
    """按站点索引替换第 index 个可变异节点。"""

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self._seen = 0

    def _hit(self) -> bool:
        if self._seen == self.index:
            return True
        self._seen += 1
        return False

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:  # noqa: N802
        repl = _BINOP_MAP.get(type(node.op))
        if repl is not None and not _is_string_concat(node):
            if self._hit():
                node.op = repl()
                return node
        return self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:  # noqa: N802
        if len(node.ops) == 1:
            repl = _CMP_MAP.get(type(node.ops[0]))
            if repl is not None:
                if self._hit():
                    node.ops = [repl()]
                    return node
        return self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:  # noqa: N802
        repl = _BOOL_MAP.get(type(node.op))
        if repl is not None:
            if self._hit():
                node.op = repl()
                return node
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:  # noqa: N802
        if isinstance(node.op, ast.Not):
            if self._hit():
                return node.operand
        return self.generic_visit(node)


_BINOP_MAP: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
}
_CMP_MAP: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
_BOOL_MAP: dict[type, type] = {ast.And: ast.Or, ast.Or: ast.And}


def _is_string_concat(node: ast.BinOp) -> bool:
    return isinstance(node.op, ast.Add) and any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) for n in (node.left, node.right)
    )


def _collect_sites(tree: ast.AST) -> list[tuple[int, str]]:
    """预序遍历收集可变异站点：[(行号, 描述)]。"""
    sites: list[tuple[int, str]] = []

    class _Collect(ast.NodeVisitor):
        def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
            repl = _BINOP_MAP.get(type(node.op))
            if repl is not None and not _is_string_concat(node):
                sites.append((getattr(node, "lineno", 0), f"binop:{type(node.op).__name__}"))
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
            if len(node.ops) == 1 and type(node.ops[0]) in _CMP_MAP:
                sites.append((getattr(node, "lineno", 0), f"cmp:{type(node.ops[0]).__name__}"))
            self.generic_visit(node)

        def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
            if type(node.op) in _BOOL_MAP:
                sites.append((getattr(node, "lineno", 0), f"bool:{type(node.op).__name__}"))
            self.generic_visit(node)

        def visit_UnaryOp(self, node: ast.UnaryOp) -> None:  # noqa: N802
            if isinstance(node.op, ast.Not):
                sites.append((getattr(node, "lineno", 0), "unary:not"))
            self.generic_visit(node)

    _Collect().visit(tree)
    return sites


def _apply_site(source: str, filename: str, site_index: int) -> tuple[bool, str]:
    """把第 site_index 个站点变异应用到源码，返回 (是否成功, 新源码)。"""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return False, source
    transformed = _Mutator(site_index).visit(tree)
    try:
        ast.fix_missing_locations(transformed)
        return True, ast.unparse(transformed)
    except Exception:
        return False, source


def generate_mutations(
    source: str, filename: str = "<source>", max_mutants: int = 20, seed: int = 42
) -> list[dict[str, Any]]:
    """生成确定性变异体（受 max_mutants 上限约束）。

    返回条目::
        {"file": filename, "lineno": int, "op": str, "source": str}
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    sites = _collect_sites(tree)
    candidates: list[dict[str, Any]] = []
    for idx, (lineno, op) in enumerate(sites):
        ok, mutated = _apply_site(source, filename, idx)
        if ok and mutated != source:
            candidates.append({"file": filename, "lineno": lineno, "op": op, "source": mutated})
    if max_mutants <= 0:
        return candidates
    if len(candidates) > max_mutants:
        rng = random.Random(seed)
        return rng.sample(candidates, max_mutants)
    return candidates


# ---------- 变异测试执行 ----------

# 复制工作区时忽略的目录（避免把门禁状态 / VCS / 依赖复制进变异体沙箱）
_MUTANT_COPY_IGNORE_DIRS = {
    ".agent_gate", ".git", ".swe-agent", "__pycache__", ".pytest_cache",
    ".venv", "venv", "node_modules", "target", ".build", "logs", "dist", "build",
}


def _make_mutant_workdir() -> Path:
    """创建 0o755 临时目录（Windows 沙箱对 0o700 目录会加拒绝 ACL）。"""
    base = Path(tempfile.gettempdir())
    for _ in range(50):
        candidate = base / "pb-mutant-{0}-{1}".format(os.getpid(), os.urandom(4).hex())
        try:
            os.makedirs(candidate, mode=0o755)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("无法创建变异测试临时目录")


def _copy_workspace_for_mutant(workspace: Path) -> Path:
    """把工作区复制到临时目录（供单个变异体执行，调用方负责清理）。"""
    tmp = _make_mutant_workdir()
    shutil.copytree(
        workspace,
        tmp / workspace.name,
        ignore=shutil.ignore_patterns(*_MUTANT_COPY_IGNORE_DIRS, "*.pyc"),
    )
    return tmp / workspace.name


def _execute_mutant(
    copy_dir: Path,
    command: list[str],
    timeout: float,
) -> tuple[int, float]:
    """在变异体副本中运行测试命令；返回 (returncode, 耗时秒)。"""
    import time

    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(copy_dir),
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return int(proc.returncode), time.monotonic() - start
    except subprocess.TimeoutExpired:
        return -99, time.monotonic() - start


def run_mutation_suite(
    workspace: Path,
    mutants: list[dict[str, Any]],
    command: list[str],
    timeout: float,
) -> dict[str, Any]:
    """逐个运行变异体，统计 killed / survived / error 与突变分数。

    - killed：测试在新变异下失败（returncode 1，变异被测试捕获）；
    - survived：测试仍全部通过（returncode 0，测试未捕获变异）；
    - error：超时 / pytest 无法运行（returncode 2/4/5 等），不计入分数。
    """
    stats = {"total": len(mutants), "killed": 0, "survived": 0, "error": 0, "score": None}
    rows: list[dict[str, Any]] = []
    for mutant in mutants:
        copy_dir = _copy_workspace_for_mutant(workspace)
        try:
            target = copy_dir / mutant["file"]
            target.write_text(mutant["source"], encoding="utf-8")
            rc, dur = _execute_mutant(copy_dir, command, timeout)
            if rc == 0:
                result = "survived"
                stats["survived"] += 1
            elif rc == 1:
                result = "killed"
                stats["killed"] += 1
            else:
                result = "error"
                stats["error"] += 1
            rows.append({
                "file": mutant["file"],
                "line": mutant["lineno"],
                "op": mutant["op"],
                "result": result,
                "returncode": rc,
                "duration_s": round(dur, 2),
            })
        finally:
            shutil.rmtree(copy_dir, ignore_errors=True)
    denom = stats["killed"] + stats["survived"]
    if denom:
        stats["score"] = round(100.0 * stats["killed"] / denom, 1)
    return {"stats": stats, "mutants": rows}


class MutationScoreValidator(SemanticValidator):
    """变异测试校验：测试通过后要求突变分数不低于阈值（防空测试 / 假断言）。

    v0.49.0 仅支持 Python；非 Python 项目返回跳过说明。需最近一次测试通过，
    且工作区可被 ``<python> -m pytest``（或配置的 command）运行。
    """

    name = "mutation_score"
    description = "Python AST 变异测试：存活变异体过多说明测试质量不足"
    stages = (4,)

    def check(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        adapter: LanguageAdapter | None = None,
    ) -> SemanticCheckResult:
        adapter = adapter or get_adapter(config, workspace)
        opts = config.semantic.mutation_score
        if getattr(adapter, "name", "") != "python":
            return SemanticCheckResult(
                True, f"变异测试仅支持 Python（当前 {getattr(adapter, 'name', '?')}），跳过",
                {"skipped": "not_python"},
            )
        tr = state.get_evidence("last_test_run") or {}
        if not tr.get("passed"):
            return SemanticCheckResult(
                True, "最近一次测试未通过，变异测试留待修复后再运行", {"skipped": "tests_not_passed"}
            )
        source_files = [
            p for p in iter_workspace_files(workspace, config) if adapter.is_source_file(p, config)
        ]
        test_files = [
            p for p in iter_workspace_files(workspace, config) if adapter.is_test_file(p, config)
        ]
        if not source_files or not test_files:
            return SemanticCheckResult(
                True, "缺少可变异源码或测试文件，变异测试跳过",
                {"skipped": "no_sources_or_tests"},
            )

        mutants: list[dict[str, Any]] = []
        for sf in source_files:
            text = sf.read_text(encoding="utf-8", errors="replace")
            rel = str(sf.relative_to(workspace))
            mutants.extend(generate_mutations(
                text, filename=rel, max_mutants=0, seed=opts.seed
            ))
        if len(mutants) > opts.max_mutants:
            rng = random.Random(opts.seed)
            mutants = rng.sample(mutants, opts.max_mutants)
        if not mutants:
            return SemanticCheckResult(
                True, "源码中未发现可变异站点（无可翻转运算符 / 比较 / 布尔），跳过",
                {"skipped": "no_mutants", "stats": {"total": 0}},
            )

        python_bin = opts.python_bin or sys.executable
        command = list(opts.command) if opts.command else [
            python_bin, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        ]
        report = run_mutation_suite(workspace, mutants, command, opts.timeout_per_mutant)
        stats = report["stats"]
        evidence = {
            "python": python_bin,
            "command": command,
            **report,
        }
        if stats["score"] is None:
            return SemanticCheckResult(
                False,
                "变异测试无法运行（全部变异体执行出错，"
                f"{stats['error']} 个 error）。请确认工作区测试可用 pytest 运行"
                "（或配置 semantic.mutation_score.command）；如确为环境问题可临时关闭该门禁",
                evidence,
            )
        if stats["score"] < opts.min_score:
            return SemanticCheckResult(
                False,
                f"变异测试未通过：突变分数 {stats['score']}% < {opts.min_score}%"
                f"（killed {stats['killed']} / survived {stats['survived']}）。"
                "测试未捕获这些变异，请补充真实断言或修正测试逻辑",
                evidence,
            )
        return SemanticCheckResult(
            True,
            f"变异测试通过：突变分数 {stats['score']}%（killed {stats['killed']} / "
            f"survived {stats['survived']} / error {stats['error']}）",
            evidence,
        )


# ---------- 注册表与运行 ----------

BUILTIN_SEMANTIC_VALIDATORS: list[SemanticValidator] = [
    RequirementCoverageValidator(),
    MutationScoreValidator(),
]

# 进程内自定义语义校验器
_custom_semantic_validators: list[SemanticValidator] = []


def register_semantic_validator(validator: SemanticValidator) -> None:
    """进程内注册自定义语义校验器（优先级高于同名内置校验器）。"""
    if not hasattr(validator, "check") or not callable(validator.check):
        raise TypeError("语义校验器必须提供 check(workspace, config, state, adapter=None) 方法")
    if not isinstance(getattr(validator, "name", ""), str) or not validator.name:
        raise ValueError("语义校验器必须提供非空 name")
    _custom_semantic_validators.append(validator)


def _coerce_semantic_validator(obj: Any) -> SemanticValidator | None:
    """把入口点对象规整为语义校验器实例（支持类 / 实例 / 工厂）。"""
    if isinstance(obj, type):
        try:
            obj = obj()
        except Exception:
            return None
    elif callable(obj) and not hasattr(obj, "check"):
        try:
            obj = obj()
        except Exception:
            return None
    if not hasattr(obj, "check"):
        return None
    return obj


def load_semantic_plugins() -> list[SemanticValidator]:
    """加载入口点注册的语义校验器 + 进程内注册的（进程内优先）。"""
    merged: list[SemanticValidator] = list(BUILTIN_SEMANTIC_VALIDATORS)
    names = {v.name for v in _custom_semantic_validators}
    merged = [v for v in merged if v.name not in names] + list(_custom_semantic_validators)
    try:
        eps = metadata.entry_points(group=SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP)
    except TypeError:  # Python 3.9- 旧接口（requires-python>=3.10，仅防御）
        eps = metadata.entry_points().get(SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP, [])
    plugin_names = {v.name for v in merged}
    for ep in eps:
        try:
            obj = _coerce_semantic_validator(ep.load())
        except Exception:
            continue
        if obj is None:
            continue
        name = getattr(obj, "name", "")
        if name in plugin_names:
            continue
        merged.append(obj)
        plugin_names.add(name)
    return merged


def run_semantic_checks(
    workspace: Path,
    config: GateConfig,
    state: Any,
    stage: int,
    adapter: LanguageAdapter | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """在当前阶段推进时运行启用的语义校验器；汇总全部结果。

    返回 ``(ok, message, evidence)``：任一校验器失败即失败（阻止阶段推进）；
    evidence 聚合各校验器明细，供状态机留痕与人工审查。
    """
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for validator in load_semantic_plugins():
        if stage not in tuple(getattr(validator, "stages", ()) or ()):
            continue
        opts: SemanticOptions = config.semantic
        # 内置校验器读取语义化字段 semantic.requirement_coverage / mutation_score；
        # 第三方校验器配置放在 semantic.plugin_options.<name>（含 enabled 开关）
        section = getattr(opts, validator.config_key(), None)
        if section is None:
            section = (opts.plugin_options or {}).get(validator.config_key())
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", False))
        else:
            enabled = bool(getattr(section, "enabled", False)) if section is not None else False
        if not enabled:
            continue
        try:
            res = validator.check(workspace, config, state, adapter)
        except Exception as exc:  # 插件自身异常不应拖垮门禁：按失败处理并给出提示
            res = SemanticCheckResult(
                False, f"语义校验器 {validator.name} 执行异常: {exc.__class__.__name__}: {exc}", {}
            )
        results.append({
            "name": validator.name,
            "ok": res.ok,
            "message": res.message,
            "evidence": res.evidence,
        })
        if not res.ok:
            failures.append(f"[{validator.name}] {res.message}")
    if failures:
        return False, "语义校验未通过：" + "；".join(failures), {"semantic_checks": results}
    if not results:
        return True, "", {}
    return True, "语义校验通过（" + ", ".join(r["name"] for r in results) + "）", {"semantic_checks": results}