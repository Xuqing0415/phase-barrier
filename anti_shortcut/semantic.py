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
- ``SpecSpecificityValidator``（spec 具体性，v0.50.0）：对 spec 做五维检查——
  具体实体密度 / 接口签名 / 明确技术决策 / 用户需求锚点 / 套话句式命中上限，
  拒绝只含章节标题的“278 字套话”式 spec。
- ``TestAssertionQualityValidator``（断言质量，v0.50.0）：AST 检查测试断言是否
  引用真实值 / 调用（非 `assert True` 等纯常数断言），防空壳测试。

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
    "SpecSpecificityValidator",
    "TestAssertionQualityValidator",
    "analyze_spec_specificity",
    "analyze_test_assertion_quality",
    "extract_request_anchors",
    "extract_concrete_entities",
    "extract_interface_signatures",
    "count_decision_phrases",
    "count_filler_hits",
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


# ---------- 深度补全第一层（v0.50.0）：spec 具体性 + 测试断言质量 ----------

# 需求锚点提取：CJK 段按双字窗口取词，过滤“泛指词 / 虚词”字符
_CJK_STOP_BIGRAMS = frozenset({
    # 空泛 / 泛指双字词：不作为需求锚点（避免“实现 / 函数”之类凑数）
    "实现", "函数", "模块", "一个", "用户", "需要", "进行", "提供", "支持",
    "使用", "设计", "方案", "系统", "服务", "功能", "方法", "接口", "完整",
    "相关", "相应", "满足", "各种", "场景", "技术", "因素", "确保", "后续",
    "阶段", "细节", "补充", "细化", "优质", "合理", "高效", "稳定", "扩展",
    "能够", "要求", "能力", "动态", "通用", "整体",
})

_CJK_STOP_CHARS = frozenset(
    "的一了是在和与为对把请要让会能将并及这那其它们被就也都很等或但而于之中上下从向以可应需做用使进"
    "行给出到过还后前内外时再才只个有没不也"
)
_LATIN_STOPWORDS = frozenset({
    "the", "to", "and", "for", "with", "a", "an", "of", "in", "on", "by",
    "at", "from", "that", "this", "please", "implement", "function", "add",
    "make", "write", "create", "need", "use", "return", "should", "will",
})
_PY_KEYWORDS = frozenset({
    "def", "class", "return", "import", "from", "if", "else", "elif", "for",
    "while", "in", "not", "and", "or", "is", "None", "True", "False", "raise",
    "with", "as", "try", "except", "finally", "lambda", "pass", "break",
    "continue", "yield", "global", "nonlocal", "assert", "del",
})

_ENTITY_RE = re.compile(
    r"(?:`[^`]+`|"
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?=\s*\()|"
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?=\s*:)|"
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?=\s*=[^=])|"
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*::|"
    r"\b(?:GET|POST|PUT|DELETE|PATCH)\s+/[A-Za-z0-9_/{{}}.-]+)"
)
_SIG_BULLET_RE = re.compile(
    r"^(?:[-*]\s*)?(?:函数|方法|接口|输入|输出|参数|返回|异常|请求|响应|路径|字段|属性)\s*[:：]"
)
_DECISION_RE = re.compile(
    r"(?:采用|选择|优先|选用|基于|引入|使用|依赖|放弃)[^。；\n]{0,40}?"
    r"(?:而非|而不是|避免|不采用|不用|替代|原因|理由|权衡|对比|兼容|迁移|便于|以免|防止|纯函数|无副作用)",
    re.IGNORECASE,
)
_LATIN_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def extract_request_anchors(request: str) -> list[str]:
    """从用户原始需求提取锚点词（latin 标识符 + 中文双字领域词，保序去重）。

    中文不做分词：对连续 CJK 段做 2-gram 窗口并丢弃含虚词字符的窗口，既能保留
    “斐波那契 / 数列”等领域词，又避免“实现 / 函数”等泛指词凑数。
    """
    anchors: list[str] = []
    for tok in _LATIN_ID_RE.findall(request):
        if tok.lower() in _LATIN_STOPWORDS:
            continue
        if tok not in anchors:
            anchors.append(tok)
    for seg in re.findall(r"[\u4e00-\u9fff]+", request):
        if len(seg) < 2:
            continue
        for i in range(len(seg) - 1):
            bigram = seg[i : i + 2]
            if any(ch in _CJK_STOP_CHARS for ch in bigram):
                continue
            if bigram in _CJK_STOP_BIGRAMS:
                continue
            if bigram not in anchors:
                anchors.append(bigram)
    return anchors


def extract_concrete_entities(spec_text: str) -> list[str]:
    """提取 spec 中代码相关的具体实体（函数 / 类 / 赋值键 / API 路径 / 反引号代码）。"""
    found: list[str] = []
    for raw in _ENTITY_RE.findall(spec_text):
        token = raw.strip().strip("`")
        if not token or token in _PY_KEYWORDS:
            continue
        if not re.match(r"^[A-Za-z_/]", token):  # 排除 REQ-001 编号等纯数字片段
            continue
        if token not in found:
            found.append(token)
    return found


def extract_interface_signatures(spec_text: str) -> list[str]:
    """统计接口签名标记：def 行 / 反引号签名 / 函数·输入·输出·参数·返回·异常·端点列表项。"""
    sigs: list[str] = []
    for line in spec_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        token = None
        if re.match(r"^(?:[-*]\s*)?def\s+[A-Za-z_]\w*\s*\(", stripped):
            token = stripped[:120]
        elif re.match(r"^(?:[-*]\s*)?(?:GET|POST|PUT|DELETE|PATCH)\s+/", stripped):
            token = stripped[:120]
        elif "`" in stripped and "(" in stripped and ")" in stripped:
            token = stripped[:120]
        elif _SIG_BULLET_RE.match(stripped) and _LATIN_ID_RE.search(stripped):
            token = stripped[:120]
        if token and token not in sigs:
            sigs.append(token)
    return sigs


def count_decision_phrases(spec_text: str) -> int:
    """统计“采用 X 避免 Y / 选择 X 而非 Y”式明确技术决策表述的命中数。"""
    return len(_DECISION_RE.findall(spec_text))


def count_filler_hits(spec_text: str, patterns: list[str] | None = None) -> int:
    """统计套话句式命中总数（patterns 缺省时用默认套话清单）。"""
    from .config import DEFAULT_SPEC_FILLER_PATTERNS

    patterns = patterns if patterns is not None else DEFAULT_SPEC_FILLER_PATTERNS
    hits = 0
    for pattern in patterns:
        try:
            hits += len(re.findall(pattern, spec_text))
        except re.error:
            continue
    return hits


def analyze_spec_specificity(
    spec_text: str,
    request: str,
    min_entities: int = 5,
    min_signatures: int = 2,
    min_decision_phrases: int = 1,
    min_requirement_anchors: int = 2,
    max_filler_hits: int = 1,
    filler_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """spec 具体性五维分析（v0.50.0）。返回含各维度 ok/值/阈值 的明细。"""
    entities = extract_concrete_entities(spec_text)
    signatures = extract_interface_signatures(spec_text)
    decisions = count_decision_phrases(spec_text)
    filler = count_filler_hits(spec_text, filler_patterns)
    anchors = extract_request_anchors(request) if request else []
    anchor_hits = [a for a in anchors if a in spec_text]
    checks: dict[str, dict[str, Any]] = {
        "concrete_entities": {
            "ok": len(entities) >= min_entities,
            "value": len(entities),
            "min": min_entities,
            "detail": entities,
        },
        "interface_signatures": {
            "ok": len(signatures) >= min_signatures,
            "value": len(signatures),
            "min": min_signatures,
            "detail": signatures,
        },
        "decision_phrases": {
            "ok": decisions >= min_decision_phrases,
            "value": decisions,
            "min": min_decision_phrases,
        },
        "filler_phrases": {
            "ok": filler <= max_filler_hits,
            "value": filler,
            "max": max_filler_hits,
        },
    }
    if request and anchors:
        checks["requirement_anchors"] = {
            "ok": len(anchor_hits) >= min_requirement_anchors,
            "value": len(anchor_hits),
            "min": min_requirement_anchors,
            "detail": anchor_hits,
        }
    return {
        "ok": all(v["ok"] for v in checks.values()),
        "checks": checks,
        "anchors_total": len(anchors),
        "request_present": bool(request),
    }


class SpecSpecificityValidator(SemanticValidator):
    """spec 具体性校验：拒绝只含章节标题的“套话 spec”。

    五维检查（阶段 1 推进时）：具体实体密度 / 接口签名数量 / 明确技术决策 /
    需求锚点命中 / 套话句式命中上限。默认关闭，启用后任一维度不达标即阻止。
    """

    name = "spec_specificity"
    description = "spec 具体性五维校验：实体 / 签名 / 决策 / 需求锚点 / 套话句式"
    stages = (1,)

    def check(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        adapter: LanguageAdapter | None = None,
    ) -> SemanticCheckResult:
        spec_path = workspace / config.spec_file
        if not spec_path.exists():
            return SemanticCheckResult(
                True, "spec 文件不存在，spec_specificity 跳过（结构校验会先行拦截）", {"skipped": "no_spec"}
            )
        opts = config.semantic.spec_specificity
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        request = ""
        if state is not None and hasattr(state, "get_evidence"):
            try:
                request = str(state.get_evidence("user_request") or "")
            except Exception:
                request = ""
        analysis = analyze_spec_specificity(
            spec_text,
            request,
            min_entities=opts.min_entities,
            min_signatures=opts.min_signatures,
            min_decision_phrases=opts.min_decision_phrases,
            min_requirement_anchors=opts.min_requirement_anchors,
            max_filler_hits=opts.max_filler_hits,
            filler_patterns=list(opts.filler_patterns),
        )
        if analysis["ok"]:
            return SemanticCheckResult(
                True,
                "spec 具体性通过（实体 {e} / 签名 {s} / 决策 {d} / 套话 {f}）".format(
                    e=analysis["checks"]["concrete_entities"]["value"],
                    s=analysis["checks"]["interface_signatures"]["value"],
                    d=analysis["checks"]["decision_phrases"]["value"],
                    f=analysis["checks"]["filler_phrases"]["value"],
                ),
                analysis,
            )
        labels = {
            "concrete_entities": "具体实体 {value}/{min}（如 `fib`、`/api/login`、`token=`）",
            "interface_signatures": "接口签名 {value}/{min}（def 行 / 函数·输入·输出·参数·返回·异常 / API 端点）",
            "decision_phrases": "明确技术决策 {value}/{min}（如“采用 X 避免 Y”）",
            "requirement_anchors": "需求锚点命中 {value}/{min}（需回应用户原始需求中的具体词）",
            "filler_phrases": "套话句式命中 {value}（上限 {max}）",
        }
        reasons = []
        for name, check in analysis["checks"].items():
            if check["ok"]:
                continue
            template = labels[name]
            reasons.append(template.format(**check))
        return SemanticCheckResult(
            False,
            "spec 具体性未通过（疑似套话 / 内容空泛）：" + "；".join(reasons)
            + "。请在 spec 中命名具体函数 / 接口 / 数据结构，给出明确技术选型与理由，并逐条回应原始需求",
            analysis,
        )


def analyze_test_assertion_quality(py_source: str) -> dict[str, Any]:
    """AST 分析测试源码的断言质量：纯常数断言（不引用任何名称 / 调用）即弱断言。

    返回结构：{"ok": bool, "weak_functions": [{"name","line","assert_lines":[...]}], "test_functions": n}
    """
    try:
        tree = ast.parse(py_source)
    except SyntaxError:
        return {"ok": True, "weak_functions": [], "test_functions": 0, "parse_error": True}
    weak: list[dict[str, Any]] = []
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        count += 1
        asserts = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
        weak_lines: list[int] = []
        for a in asserts:
            references = [
                n for n in ast.walk(a)
                if isinstance(n, (ast.Name, ast.Attribute, ast.Call, ast.Subscript))
            ]
            if not references:
                weak_lines.append(getattr(a, "lineno", 0))
        if asserts and len(weak_lines) == len(asserts):
            weak.append({
                "name": node.name,
                "line": getattr(node, "lineno", 0),
                "assert_lines": weak_lines,
            })
    return {"ok": not weak, "weak_functions": weak, "test_functions": count, "parse_error": False}


class TestAssertionQualityValidator(SemanticValidator):
    """测试断言质量校验：拒绝 `assert True` / `assert 1 == 1` 等纯常数断言。

    “纯常数断言”指断言表达式中不引用任何名称 / 属性 / 调用 / 下标（即不来自被测
    代码的变量或返回值）。默认关闭；strict 模式（默认）下任何 test 函数含纯常数
    断言即拒绝。
    """

    name = "test_assertion_quality"
    description = "测试断言质量：拒绝 assert True 等纯常数断言（仅 Python）"
    stages = (2,)

    def check(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        adapter: LanguageAdapter | None = None,
    ) -> SemanticCheckResult:
        adapter = adapter or get_adapter(config, workspace)
        if getattr(adapter, "name", "") != "python":
            return SemanticCheckResult(
                True, f"断言质量校验仅支持 Python（当前 {getattr(adapter, 'name', '?')}），跳过",
                {"skipped": "not_python"},
            )
        opts = config.semantic.test_assertion_quality
        if not opts.strict:
            return SemanticCheckResult(True, "断言质量校验未启用 strict，跳过", {"skipped": "not_strict"})
        failures: list[dict[str, Any]] = []
        for tf in iter_workspace_files(workspace, config):
            if not adapter.is_test_file(tf, config):
                continue
            text = tf.read_text(encoding="utf-8", errors="replace")
            info = analyze_test_assertion_quality(text)
            if info.get("parse_error"):
                continue  # 语法问题由结构校验拦截
            for fn in info["weak_functions"]:
                failures.append({"file": str(tf.relative_to(workspace)), **fn})
        if not failures:
            return SemanticCheckResult(
                True, "断言质量通过：所有 test 函数的断言均引用实际值 / 调用", {"weak_functions": []}
            )
        detail = "；".join(
            f"{f['file']}:{f['name']}(L{f['line']}) 纯常数断言行 {f['assert_lines']}" for f in failures
        )
        return SemanticCheckResult(
            False,
            "断言质量未通过：以下测试函数只含纯常数断言（如 `assert True`），未引用被测代码："
            + detail + "。请让断言比较真实的函数返回值 / 状态，例如 `assert fib(10) == 55`",
            {"weak_functions": failures},
        )


# ---------- 注册表与运行 ----------

BUILTIN_SEMANTIC_VALIDATORS: list[SemanticValidator] = [
    RequirementCoverageValidator(),
    MutationScoreValidator(),
    SpecSpecificityValidator(),
    TestAssertionQualityValidator(),
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