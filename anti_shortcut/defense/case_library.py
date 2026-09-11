"""拦截案例库（路径 4，v0.59.0）：从拦截案例中学习新的逃逸模式。

设计原则（见 ``docs/learning-defense.md``）：

- **采集**：每次防线触发（拦截）时把案例追加写入 JSONL 案例库；
- **提取**：``scripts/analyze_cases.py`` 按防线 / 触发类型分组，提取高频模式，
  并与现有规则库比对找出「新出现的模式」；
- **更新**：``scripts/update_rules.py`` 产出可直接评审的规则 / 提示词更新建议，
  **不自动合入**——必须人工审核后以 PR 形式提交，避免自动引入误报或恶意规则。

案例库默认是**按工作区**的（``<workspace>/.agent_gate/defense/case_library.jsonl``）；
需要跨任务汇总时把 ``defense.case_library.shared_library`` 指向一个共享路径
（同一台机器 / 同一 CI 的所有工作区共用），分析脚本可以一次读多个库。

红线：采集失败绝不影响门禁主流程——任何异常都被吞掉，只记日志。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import CaseLibraryOptions, GateConfig
from ._common import append_jsonl, evidence_path, now_iso, read_jsonl

__all__ = [
    "CaseRecord",
    "classify_trigger",
    "build_case_id",
    "record_case",
    "load_cases",
    "iter_case_libraries",
    "summarize_cases",
    "capture_defense_failure",
    "capture_escape",
    "case_texts",
]

MAX_INPUT_CHARS = 800


@dataclass
class CaseRecord:
    """一条拦截案例。"""

    case_id: str
    at: str
    defense_line: str
    trigger_type: str
    agent_input: str
    blocked_by: str
    blocked_reason: str
    escape_attempt: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_case_id(*parts: str) -> str:
    """案例 ID：对「防线 + 触发类型 + 输入」取哈希，保证同一案例重跑幂等。"""
    joined = "\u0000".join(str(p) for p in parts)
    return "case-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def classify_trigger(
    line_name: str, message: str, evidence: dict[str, Any] | None = None
) -> str:
    """把一次防线失败归类成「触发类型」，供后续模式提取分组。

    分类故意做得粗糙但稳定：分析脚本按类型分组后做模式提取，类型只需要
    「同类攻击落在一起」，不需要精确到具体变体。
    """
    ev = evidence or {}
    if line_name == "behavior_audit":
        violations = ev.get("violations") or []
        if any(v.get("source") == "deliverable" for v in violations if isinstance(v, dict)):
            return "untraced_write"
        categories = sorted(
            {
                str(v.get("category"))
                for v in violations
                if isinstance(v, dict) and v.get("category")
            }
        )
        if categories:
            return "forbidden_op:" + ",".join(categories)
        if ev.get("missing_test_command"):
            return "missing_test_command"
        return "audit_other"
    if line_name == "requirement_template":
        if "空话" in message or "重复条目" in message or "不是非空字符串" in message:
            return "vague_template"
        if "缺少需求模板" in message:
            return "template_missing"
        return "template_invalid"
    if line_name == "formal_check":
        if ev.get("static_contradictions"):
            return "static_contradiction"
        if ev.get("variable_aliases"):
            return "alias_contradiction"
        return "formal_invalid"
    if line_name == "dual_review":
        if "fail_closed" in message or "调用失败" in message or "未配置环境变量" in message:
            return "semantic_unavailable"
        return "semantic_tamper"
    if line_name == "human_review":
        return "risk_sampling"
    return "other"


def _as_input_text(value: Any) -> str:
    if isinstance(value, str):
        return value[:MAX_INPUT_CHARS]
    return json.dumps(value, ensure_ascii=False, default=str)[:MAX_INPUT_CHARS]


def case_texts(case: dict[str, Any]) -> list[str]:
    """把一条案例里可用于「反查」的文本取出来（载荷 + 违规片段 + 冲突条目）。"""
    texts = [str(case.get("agent_input") or "")]
    context = case.get("context") or {}
    if isinstance(context, dict):
        for violation in context.get("violations") or []:
            if isinstance(violation, dict):
                texts.append(str(violation.get("snippet") or ""))
                texts.append(str(violation.get("path") or ""))
        for issue in context.get("issues") or []:
            texts.append(str(issue))
    return [t for t in texts if t]


def record_case(path: Path, record: CaseRecord) -> Path:
    """把案例追加进 JSONL 案例库（同一 ``case_id`` 只写一次）。"""
    path = Path(path)
    if any(entry.get("case_id") == record.case_id for entry in read_jsonl(path)):
        return path
    append_jsonl(path, record.to_dict())
    return path


def iter_case_libraries(workspace: Path, config: GateConfig) -> list[Path]:
    """返回该工作区相关的案例库路径（按工作区库 + 可选共享库）。"""
    opts: CaseLibraryOptions = config.defense.case_library
    if not opts.enabled:
        return []
    out = [evidence_path(workspace, config, opts.library_file)]
    if opts.shared_library:
        shared = Path(str(opts.shared_library))
        if not shared.is_absolute():
            shared = workspace / shared
        out.append(shared)
    return out


def load_cases(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    """读取一个或多个 JSONL 案例库（跳过不存在的文件）。"""
    cases: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            cases.extend(read_jsonl_dir(path))
            continue
        cases.extend(read_jsonl(path))
    return cases


def read_jsonl_dir(path: Path) -> list[dict[str, Any]]:
    """读取目录下所有 ``*.jsonl``（递归）。"""
    out: list[dict[str, Any]] = []
    for child in sorted(path.rglob("*.jsonl")):
        out.extend(read_jsonl(child))
    return out


def summarize_cases(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """按防线 / 触发类型 / 是否逃逸尝试汇总。"""
    by_line: dict[str, int] = {}
    by_trigger: dict[str, int] = {}
    escapes = 0
    for case in cases:
        line = str(case.get("defense_line") or "unknown")
        trigger = str(case.get("trigger_type") or "unknown")
        by_line[line] = by_line.get(line, 0) + 1
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1
        if case.get("escape_attempt"):
            escapes += 1
    return {
        "total": len(cases),
        "escape_attempts": escapes,
        "by_defense_line": dict(sorted(by_line.items())),
        "by_trigger_type": dict(sorted(by_trigger.items())),
    }


def capture_escape(
    workspace: Path,
    config: GateConfig,
    *,
    technique: str,
    target_defense: int,
    payload: str,
    category: str = "",
    requirement: str = "",
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """红队**逃逸成功**时把攻击载荷写入案例库（学习闭环的训练数据）。

    与 :func:`capture_defense_failure` 的区别：那条路径记录的是「已被拦下」的案例，
    这条记录的是「本该拦住却没拦住」的案例——正是路径 4 要学习的新模式来源。

    与采集钩子一致：任何异常都不会向外抛出。
    """
    try:
        opts: CaseLibraryOptions = config.defense.case_library
        if not opts.enabled:
            return None
        payload = _as_input_text(payload)
        context: dict[str, Any] = {
            "technique": technique,
            "target_defense": int(target_defense),
            "escaping": True,
        }
        if category:
            context["category"] = category
        if requirement:
            context["user_request"] = str(requirement)[:400]
        if extra:
            context.update(extra)
        record = CaseRecord(
            case_id=build_case_id("escape", technique, payload),
            at=now_iso(),
            defense_line=f"defense{int(target_defense)}",
            trigger_type=f"escape:{technique}",
            agent_input=payload,
            blocked_by="",
            blocked_reason=str(message)[:600],
            escape_attempt=True,
            context=context,
        )
        targets = iter_case_libraries(workspace, config)
        for target in targets:
            record_case(target, record)
        return targets[0] if targets else None
    except Exception:  # noqa: BLE001 - 采集绝不影响红队主流程
        return None


def capture_defense_failure(
    workspace: Path,
    config: GateConfig,
    line_name: str,
    message: str,
    evidence: dict[str, Any] | None,
    state: Any = None,
    from_stage: int | None = None,
    to_stage: int | None = None,
) -> Path | None:
    """防线失败时的采集钩子（``defense.case_library.enabled`` 为真才生效）。

    任何异常都不会向外抛出：案例库永远不能影响门禁判定。
    """
    try:
        opts: CaseLibraryOptions = config.defense.case_library
        if not opts.enabled:
            return None
        trigger_type = classify_trigger(line_name, message, evidence)
        agent_input = ""
        if evidence:
            agent_input = _as_input_text(
                evidence.get("violations") or evidence.get("issues") or message
            )
        context: dict[str, Any] = {}
        if state is not None:
            try:
                context["user_request"] = str(state.get_evidence("user_request") or "")[:400]
            except Exception:  # noqa: BLE001
                pass
        if from_stage is not None and to_stage is not None:
            context["transition"] = f"{from_stage}->{to_stage}"
        if evidence:
            context["violations"] = (evidence.get("violations") or [])[:5]
            context["issues"] = (evidence.get("issues") or [])[:5]
        record = CaseRecord(
            case_id=build_case_id(line_name, trigger_type, agent_input),
            at=now_iso(),
            defense_line=line_name,
            trigger_type=trigger_type,
            agent_input=agent_input,
            blocked_by=line_name,
            blocked_reason=str(message)[:600],
            escape_attempt=False,
            context=context,
        )
        targets = iter_case_libraries(workspace, config)
        for target in targets:
            record_case(target, record)
        return targets[0] if targets else None
    except Exception:  # noqa: BLE001 - 采集绝不影响门禁
        return None
