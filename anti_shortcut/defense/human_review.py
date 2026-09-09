"""防线 5：概率人工复核（阶段 4/5 -> 6 交付前，v0.52.0）。

所有自动化防线都存在理论上的绕过可能。本防线在防线 1-4 通过后，依据
**风险分数**做确定性抽样：风险越高越可能进入人工复核队列；超过阈值必抽；
低风险自动放行。被抽中的任务生成 ``human_review_request.json``（包含原始需求、
spec 摘要、双模型结论、形式化结果、行为比对报告），由人工通过
``python -m anti_shortcut review-approve`` 放行后重新推进即可交付。
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from ..config import GateConfig, HumanReviewOptions
from ._base import DefenseCheckResult, DefenseLine
from ._common import (
    evidence_path,
    now_iso,
    read_json,
    read_jsonl,
    write_evidence,
)


def _request_id(seed_text: str) -> str:
    return hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16]


def _load_approvals(approval_path: Path) -> list[dict[str, Any]]:
    data = read_json(approval_path)
    entries = data.get("approvals", []) if isinstance(data, dict) else []
    return entries if isinstance(entries, list) else []


def approve_request(
    workspace: Path, config: GateConfig, request_id: str, reason: str = ""
) -> Path:
    """记录一次人工复核通过（供 CLI ``review-approve`` 与测试使用）。"""
    opts: HumanReviewOptions = config.defense.human_review
    path = evidence_path(workspace, config, opts.approval_file)
    entries = _load_approvals(path)
    entries.append(
        {
            "request_id": request_id,
            "approved": True,
            "at": now_iso(),
            "reason": reason,
        }
    )
    write_evidence(
        workspace,
        config,
        opts.approval_file,
        {"approvals": entries},
    )
    return path


def _spec_summary(workspace: Path, config: GateConfig, limit: int = 800) -> str:
    spec_path = workspace / config.spec_file
    if not spec_path.is_file():
        return "(无 spec)"
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def compute_risk_score(
    workspace: Path, config: GateConfig, state: Any
) -> tuple[int, dict[str, Any]]:
    """按需求复杂度 / 双模型置信度 / 形式化是否启用 / 审计偏离 / 变更规模打分（0-100）。"""
    parts: dict[str, Any] = {}

    # 1) 需求复杂度（0-35）：目标长度、禁止行为 / 验收条目越多越复杂
    req = state.get_evidence("requirement_template")
    user_request = str(state.get_evidence("user_request") or "")
    length = len(user_request)
    if isinstance(req, dict):
        forbidden_n = len(req.get("forbidden") or [])
        acceptance_n = len(req.get("acceptance") or [])
        interface_n = len(req.get("interfaces") or [])
    else:
        forbidden_n = acceptance_n = interface_n = 0
    complexity = min(
        35,
        int(length / 60)
        + 5 * min(forbidden_n, 4)
        + 2 * min(acceptance_n, 3)
        + 2 * min(interface_n, 3),
    )
    parts["complexity"] = complexity

    # 2) 双模型置信度（0-20）：置信度越低风险越高；未跑（关闭）不扣分，由防线 2 兜底
    dual = read_json(evidence_path(workspace, config, "dual_review.json"))
    dual_conf = 0.0
    if dual:
        confs = []
        for key in ("reviewer_a", "reviewer_b"):
            parsed = (dual.get("parsed") or {}).get(key) or {}
            conf = parsed.get("confidence")
            if isinstance(conf, (int, float)):
                confs.append(float(conf))
        if confs:
            dual_conf = sum(confs) / len(confs)
    dual_score = int(round((1.0 - dual_conf) * 20))
    parts["dual_confidence"] = round(dual_conf, 3)
    parts["dual_penalty"] = dual_score

    # 3) 形式化是否启用（0-15）：未启用形式化校验的任务风险更高
    formal_enabled = bool(config.defense.formal_check.enabled)
    parts["formal_enabled"] = formal_enabled
    parts["formal_penalty"] = 0 if formal_enabled else 15

    # 4) 行为审计偏离数（0-20）
    diff = read_json(evidence_path(workspace, config, "behavior_diff.json"))
    violations_n = len((diff or {}).get("violations") or [])
    audit_score = min(20, violations_n * 5)
    parts["audit_violations"] = violations_n
    parts["audit_penalty"] = audit_score

    # 5) 变更规模（0-10）：trace 中写入过不同文件的数量
    opts_ba = config.defense.behavior_audit
    trace_path = evidence_path(workspace, config, opts_ba.trace_file)
    files: set[str] = set()
    for record in read_jsonl(trace_path):
        if record.get("tool") not in ("write_file", "write", "apply_patch"):
            continue
        args = record.get("args") or {}
        path = args.get("path") or args.get("file") if isinstance(args, dict) else None
        if path:
            files.add(str(path))
    change_score = min(10, len(files) * 2)
    parts["files_touched"] = len(files)
    parts["change_penalty"] = change_score

    score = min(100, complexity + dual_score + parts["formal_penalty"] + audit_score + change_score)
    parts["total"] = score
    return score, parts


class HumanReviewLine(DefenseLine):
    """防线 5 实现：交付前概率抽样人工复核。"""

    name = "human_review"
    trigger = ((4, 6), (5, 6))

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        opts: HumanReviewOptions = config.defense.human_review
        user_request = str(state.get_evidence("user_request") or "")
        seed_text = f"{workspace}|{user_request}|{to_stage}"
        score, parts = compute_risk_score(workspace, config, state)
        request_id = _request_id(seed_text)

        must_review = False
        probability = 0.0
        if score >= opts.force_above_score:
            must_review = True
            probability = 1.0
        elif score < opts.auto_approve_below_score:
            probability = 0.0
        else:
            probability = min(opts.sample_rate, score / 100.0 * opts.sample_rate)
            rng = (
                random.Random(request_id)
                if opts.deterministic
                else random.Random()
            )
            must_review = rng.random() < probability

        approval_path = evidence_path(workspace, config, opts.approval_file)
        evidence: dict[str, Any] = {
            "at": now_iso(),
            "risk_score": score,
            "risk_breakdown": parts,
            "sample_rate": opts.sample_rate,
            "sampled": must_review,
            "probability": round(probability, 4),
            "request_id": request_id,
        }
        if must_review:
            approved = [
                e
                for e in _load_approvals(approval_path)
                if e.get("request_id") == request_id and e.get("approved")
            ]
            if approved:
                evidence["approved"] = True
                return DefenseCheckResult(
                    True,
                    f"防线 5（人工复核）通过：任务 {request_id} 已获得人工批准",
                    evidence,
                    request_id=request_id,
                )
            # 生成供人类决策的复核请求包
            payload = {
                "request_id": request_id,
                "risk_score": score,
                "risk_breakdown": parts,
                "created_at": now_iso(),
                "user_request": user_request[:2000],
                "requirement_template": state.get_evidence("requirement_template"),
                "spec_summary": _spec_summary(workspace, config),
                "dual_review": read_json(evidence_path(workspace, config, "dual_review.json")),
                "formal_check": read_json(evidence_path(workspace, config, "tla_check.json")),
                "behavior_diff": read_json(evidence_path(workspace, config, "behavior_diff.json")),
                "attention_points": [
                    "请核对 spec 是否完整承载需求（可对照双模型意见）",
                    "请核对 trace 中是否出现过被禁止的操作",
                    "请在批准后运行：python -m anti_shortcut review-approve"
                    f" --request-id {request_id} --workspace {workspace}",
                ],
            }
            write_evidence(workspace, config, "human_review_request.json", payload)
            return DefenseCheckResult(
                False,
                f"防线 5（人工复核）未放行：任务风险分 {score}/100 命中人工复核抽样"
                f"（请求 ID {request_id}）。复核请求已写入 "
                f".agent_gate/defense/human_review_request.json；人工通过后运行 "
                f"`python -m anti_shortcut review-approve --request-id {request_id}` "
                f"再重新推进阶段",
                evidence,
                request_id=request_id,
            )
        return DefenseCheckResult(
            True,
            f"防线 5（人工复核）自动放行：风险分 {score}/100 未命中抽样"
            f"（probability={probability:.2%}）",
            evidence,
            request_id=request_id,
        )
