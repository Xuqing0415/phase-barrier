"""防线 4：事后链路审计（阶段 4/5 -> 6 交付前，v0.52.0）。

静态门禁只能保证文档合规，无法阻止 Agent 在运行阶段偏离 spec。本防线把
``<workspace>/.agent_gate/defense/trace.jsonl`` 中的工具调用轨迹（由 Skill 包装
的 write_file / execute_command / advance_stage 自动记录，参数已脱敏）与
spec / 需求模板声明的「禁止操作」比对：

- 执行了禁止操作（如 spec 禁止改数据库但 trace 出现 ``ALTER TABLE``）-> 拒绝交付；
- 声称已运行测试但轨迹里没有任何测试命令 -> 默认警告，可配置为拒绝；
- 比对报告落盘 ``behavior_diff.json`` 供人工复核（防线 5）引用。

机器可自动判定的禁止类别（关键词命中即启用）：
``db_schema``（改表结构）、``network``（网络访问）、``file_delete``（删除文件）、
``secret``（硬编码密钥）。其余自定义类别通过
``defense.behavior_audit.extra_forbidden_patterns`` 配置（类别名 -> 正则列表）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import BehaviorAuditOptions, GateConfig
from ._base import DefenseCheckResult, DefenseLine
from ._common import evidence_path, now_iso, read_jsonl, write_evidence

# 关键词（含中文）命中 spec / 模板的“禁止…”句子时，激活对应类别
BUILTIN_FORBIDDEN: dict[str, dict[str, Any]] = {
    "db_schema": {
        "triggers": ["数据库", "表结构", "数据库结构", "schema", "数据模型", "迁移"],
        "patterns": [
            r"(?i)\balter\s+table\b",
            r"(?i)\bdrop\s+table\b",
            r"(?i)\btruncate\s+table\b",
            r"(?i)\bdrop\s+database\b",
            r"(?i)\bmigrat(e|ion)\b",
        ],
    },
    "network": {
        "triggers": ["网络", "联网", "外部访问", "外网", "网络请求", "发送请求"],
        "patterns": [
            r"(?i)\brequests\.(get|post|put|delete|patch)\b",
            r"(?i)\bhttpx\.\w+",
            r"(?i)\burllib(\.request)?\b",
            r"(?i)\b(aiohttp|http\.client)\b",
            r"(?i)https?://",
            r"(?i)\bcurl\b",
            r"(?i)\bwget\b",
            r"(?i)\bInvoke-WebRequest\b",
            r"(?i)\bsocket\.(create_connection|connect)\b",
        ],
    },
    "file_delete": {
        "triggers": ["删除文件", "删除代码", "删除", "移除文件"],
        "patterns": [
            r"(?i)\brm\s+-[a-z]*r",
            r"(?i)\brmdir\b",
            r"(?i)\bos\.remove\b",
            r"(?i)\bos\.unlink\b",
            r"(?i)\bpathlib\.\w*\.unlink\b",
            r"(?i)\bdel\s+[\w./\\-]+\.(py|js|ts|java|go|rs|c|cpp|h)\b",
        ],
    },
    "secret": {
        "triggers": ["硬编码", "密钥", "凭据", "口令明文"],
        "patterns": [
            r"(?i)\bsk-[A-Za-z0-9]{16,}\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"(?i)\bghp_[A-Za-z0-9]{20,}\b",
            r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----",
        ],
    },
}

_FORBID_MARKERS = ("禁止", "不允许", "不得", "严禁", "禁止性")
_TEST_RE = re.compile(
    r"(?:python(?:3)?\s+-m\s+)?pytest\b|python(?:3)?\s+-m\s+unittest\b|"
    r"\bnpm\s+test\b|\bnpx\s+(?:jest|vitest|mocha|playwright)\b|"
    r"\bgo\s+test\b|\bcargo\s+test\b|\bgradle\w*\s+test\b|\bmvn\w*\s+test\b|\btox\b"
)


def _forbidden_statements(text: str) -> list[str]:
    """从 spec / 需求文本提取包含“禁止/不得/不允许/严禁”的句子。"""
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-*#").strip()
        if any(marker in line for marker in _FORBID_MARKERS):
            if len(line) > 3:
                out.append(line[:200])
    return out


def _record_text(record: dict[str, Any], scan_write_content: bool) -> str:
    """把单条 trace 记录序列化为可扫描文本（默认含写入内容；可关闭）。"""
    rec = dict(record)
    args = rec.get("args")
    if isinstance(args, dict) and not scan_write_content:
        args = {k: v for k, v in args.items() if k not in ("content", "text")}
        rec["args"] = args
    return json.dumps(rec, ensure_ascii=False, default=str)


def _command_of(record: dict[str, Any]) -> str:
    args = record.get("args")
    if isinstance(args, dict):
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, list):
            return " ".join(str(c) for c in cmd)
        return str(cmd)
    return ""


class BehaviorAuditLine(DefenseLine):
    """防线 4 实现：交付前比对 trace 与 spec 禁止操作。"""

    name = "behavior_audit"
    trigger = ((4, 6), (5, 6))

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        opts: BehaviorAuditOptions = config.defense.behavior_audit
        trace_path = evidence_path(workspace, config, opts.trace_file)
        records = read_jsonl(trace_path)

        # 汇总 spec / 需求模板的禁止表述，确定激活类别
        texts: list[str] = []
        spec_path = workspace / config.spec_file
        if spec_path.is_file():
            texts.append(spec_path.read_text(encoding="utf-8", errors="replace"))
        req = state.get_evidence("requirement_template")
        if isinstance(req, dict):
            for forbidden in req.get("forbidden", []) or []:
                texts.append(f"禁止：{forbidden}")
            for acceptance in req.get("acceptance", []) or []:
                texts.append(acceptance)
        statements = [s for t in texts for s in _forbidden_statements(t)]
        joined = "\n".join(texts)

        active: dict[str, list[str]] = {}
        for category, spec in BUILTIN_FORBIDDEN.items():
            if any(trig in joined for trig in spec["triggers"]):
                active[category] = list(spec["patterns"])
        # 用户自定义类别（无需关键词，直接启用正则）
        for category, patterns in (opts.extra_forbidden_patterns or {}).items():
            active.setdefault(str(category), []).extend(patterns)

        violations: list[dict[str, Any]] = []
        missing_test_command = True
        seen_test_command = False
        for record in records:
            text = _record_text(record, opts.scan_write_content)
            if record.get("tool") in ("execute_command", "exec", "shell"):
                cmd = _command_of(record)
                if cmd and _TEST_RE.search(cmd):
                    seen_test_command = True
            for category, patterns in active.items():
                for pattern in patterns:
                    try:
                        if re.search(pattern, text):
                            violations.append(
                                {
                                    "category": category,
                                    "pattern": pattern,
                                    "tool": record.get("tool"),
                                    "ts": record.get("ts"),
                                    "stage": record.get("stage"),
                                    "snippet": text[:300],
                                }
                            )
                    except re.error:
                        continue

        missing_test_command = not seen_test_command
        statements = list(dict.fromkeys(statements))
        evidence: dict[str, Any] = {
            "at": now_iso(),
            "trace_file": str(trace_path),
            "records_scanned": len(records),
            "forbidden_statements": statements,
            "active_categories": sorted(active),
            "violations": violations,
            "seen_test_command": seen_test_command,
            "missing_test_command": missing_test_command,
        }
        write_evidence(workspace, config, "behavior_diff.json", evidence)

        if violations:
            detail = "；".join(
                f"[{v['category']}] 工具 {v.get('tool')} 命中 {v['pattern']}"
                for v in violations[:10]
            )
            return DefenseCheckResult(
                False,
                f"防线 4（行为审计）未通过：trace 中检测到 {len(violations)} 处"
                f" spec 禁止的操作——{detail}。请回退违规改动并重新走对应阶段",
                evidence,
            )
        if missing_test_command and opts.deny_missing_test_command:
            return DefenseCheckResult(
                False,
                "防线 4（行为审计）未通过：trace 中没有运行任何测试命令，"
                "但 deny_missing_test_command=true。请通过包装后的 execute_command "
                "运行测试并确认记录",
                evidence,
            )
        msg = "防线 4（行为审计）通过"
        if missing_test_command:
            msg += "（注意：trace 中未发现测试命令，默认仅警告）"
        return DefenseCheckResult(True, msg, evidence)
