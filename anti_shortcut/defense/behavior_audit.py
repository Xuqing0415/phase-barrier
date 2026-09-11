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
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..config import BehaviorAuditOptions, GateConfig
from ._base import DefenseCheckResult, DefenseLine
from ._common import (
    evidence_path,
    load_yaml_or_json,
    now_iso,
    read_jsonl,
    write_evidence,
)

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
            r"(?i)\brename\s+table\b",
            r"(?i)\bcreate\s+(unique\s+)?index\b",
            r"(?i)\bdrop\s+index\b",
            r"(?i)\bcreate\s+table\b",
            r"(?i)\balter\s+column\b",
            r"(?i)\b(add|modify|change|drop)\s+column\b",
        ],
    },
    "network": {
        "triggers": ["网络", "联网", "外部访问", "外网", "网络请求", "发送请求", "API 调用"],
        "patterns": [
            r"(?i)\brequests\.\w+",
            r"(?i)\burllib3\b",
            r"(?i)\burllib(\.request)?\b",
            r"(?i)\bhttpx\.\w+",
            r"(?i)\b(aiohttp|http\.client)\b",
            r"(?i)https?://",
            r"(?i)\bcurl\b",
            r"(?i)\bwget\b",
            r"(?i)\bInvoke-(WebRequest|RestMethod)\b",
            r"(?i)\bsocket\.(socket|create_connection|connect)\b",
            r"(?i)\b(smtplib|ftplib|poplib|imaplib|telnetlib|paramiko|pycurl|websocket)\b",
            r"(?i)\bwebbrowser\.open\b",
        ],
    },
    "file_delete": {
        "triggers": ["删除文件", "删除代码", "删除", "移除文件", "清理文件"],
        "patterns": [
            # shell：rm -f / rm -rf / rm notes.txt / find -delete / git clean / rsync --delete
            r"(?i)(?<![\w.])rm\s+-[a-zA-Z]",
            r"(?i)(?<![\w.])rm\s+[\w./\\-]+\.(py|js|ts|txt|db|sql|json|ya?ml|md|log|cfg|ini)\b",
            r"(?i)\brmdir\b",
            r"(?i)\bfind\b[^\n]*\s-delete\b",
            r"(?i)\bgit\s+clean\b",
            r"(?i)\brsync\b[^\n]*--delete\b",
            r"(?i)\bRemove-Item\b",
            r"(?i)\bClear-Content\b",
            # Python：shutil / os / pathlib / 裸 unlink
            r"(?i)\bshutil\.rmtree\b",
            r"(?i)\bos\.(remove|unlink|rmdir|removedirs)\b",
            r"(?i)\.unlink\s*\(",
            r"(?i)\.rmdir\s*\(",
            r"(?i)\bpathlib\.\w*\.unlink\b",
            r"(?i)\bdel\s+[\w./\\-]+\.(py|js|ts|java|go|rs|c|cpp|h)\b",
        ],
    },
    "secret": {
        "triggers": ["硬编码", "密钥", "凭据", "口令明文", "明文密码", "敏感信息"],
        "patterns": [
            r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bAIza[0-9A-Za-z_\-]{30,}\b",
            r"(?i)\bghp_[A-Za-z0-9]{20,}\b",
            r"(?i)\bgho_[A-Za-z0-9]{20,}\b",
            r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}\b",
            r"(?i)\bglpat-[A-Za-z0-9_\-]{16,}\b",
            r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{10,}\b",
            r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----",
            # trace 记录会把内容 JSON 序列化（引号变成 \" ），因此引号前后允许一个反斜杠
            r"(?i)\b(client_secret|access_key|secret_key|private_key)"
            r"\s*[=:]\s*\\?[\'\"][A-Za-z0-9_\-./+=]{8,}\\?[\'\"]",
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


def _record_path(record: dict[str, Any]) -> str:
    args = record.get("args")
    if isinstance(args, dict):
        value = args.get("path") or args.get("file")
        if value is not None:
            return str(value)
    return ""


def _matches_any(name: str, patterns: set[str]) -> bool:
    """目录名是否命中排除规则（``exclude_dirs`` 支持 ``*`` glob）。"""
    lowered = name.lower()
    return any(fnmatch(lowered, pattern) for pattern in patterns)


def _iter_deliverables(workspace: Path, opts: BehaviorAuditOptions) -> list[Path]:
    """列出工作区里的交付物源码文件（跳过依赖 / 构建产物 / 门禁目录）。

    v0.57.0：即使写入绕过了工具包装器（trace 无记录），或 trace 里的写入内容被
    截断（默认 4000 字符），交付物本身仍在工作区里，扫描它才能兜住这类逃逸。

    遍历必须是**容错**的：工作区里可能存在不可读目录（权限 / ACL / 沙箱），
    ``Path.rglob`` 遇到这类目录会直接抛 ``PermissionError``，从而把整道防线
    变成「审计异常 = fail-closed 拒绝交付」，那会误伤正常任务。
    """
    extensions = {str(e).lower() for e in (opts.source_extensions or [])}
    excluded = {str(d).lower() for d in (opts.exclude_dirs or [])}
    if not extensions:
        return []
    out: list[Path] = []
    stack = [workspace]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:  # 目录不可读：跳过而不是让审计整体失败
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if not _matches_any(entry.name, excluded):
                        stack.append(entry)
                elif entry.is_file() and entry.suffix.lower() in extensions:
                    out.append(entry)
            except OSError:  # pragma: no cover - 单条目不可读时跳过
                continue
    return sorted(out)


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

        # 学习型规则文件（路径 4）：案例分析 -> 人工评审 -> 在这里生效
        learned_file = ""
        if opts.extra_forbidden_patterns_file:
            rules_path = Path(str(opts.extra_forbidden_patterns_file))
            if not rules_path.is_absolute():
                rules_path = workspace / rules_path
            learned_file = str(rules_path)
            learned = load_yaml_or_json(rules_path)
            if learned is None:
                # fail-closed：配了规则文件却读不到 / 解析不了 -> 拒绝交付
                return DefenseCheckResult(
                    False,
                    "防线 4（行为审计）未通过：配置了 extra_forbidden_patterns_file="
                    f"{learned_file}，但文件缺失或无法解析（YAML/JSON 格式错误）。"
                    "请修复规则文件（缺少规则会静默放行，因此这里选择 fail-closed）",
                    {
                        "at": now_iso(),
                        "extra_forbidden_patterns_file": learned_file,
                        "rules_loaded": False,
                    },
                )
            for category, patterns in (learned.get("forbidden_patterns") or {}).items():
                if isinstance(patterns, list):
                    active.setdefault(str(category), []).extend(str(p) for p in patterns)

        violations: list[dict[str, Any]] = []
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
                                    "source": "trace",
                                    "tool": record.get("tool"),
                                    "path": _record_path(record),
                                    "ts": record.get("ts"),
                                    "stage": record.get("stage"),
                                    "snippet": text[:300],
                                }
                            )
                    except re.error:
                        continue

        # 交付物兜底扫描：trace 里看不到的写入（绕过包装器 / 内容被截断）在文件里仍在
        deliverable_files = _iter_deliverables(workspace, opts) if opts.scan_deliverables else []
        traced_names = {
            Path(p).name for p in (_record_path(r) for r in records) if p
        }
        untraced: list[str] = []
        for path in deliverable_files:
            rel = path.relative_to(workspace).as_posix()
            if path.name not in traced_names:
                untraced.append(rel)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - 读取失败不应影响门禁
                continue
            for category, patterns in active.items():
                # 该文件的同类问题已由 trace 报过就不重复计数
                already = any(
                    v.get("category") == category
                    and Path(str(v.get("path") or "")).name == path.name
                    for v in violations
                )
                if already:
                    continue
                for pattern in patterns:
                    try:
                        if re.search(pattern, content):
                            violations.append(
                                {
                                    "category": category,
                                    "pattern": pattern,
                                    "source": "deliverable",
                                    "tool": "deliverable",
                                    "path": rel,
                                    "stage": None,
                                    "snippet": content[:300],
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
            "extra_forbidden_patterns_file": learned_file,
            "deliverables_scanned": [
                p.relative_to(workspace).as_posix() for p in deliverable_files
            ],
            "untraced_artifacts": untraced,
            "violations": violations,
            "seen_test_command": seen_test_command,
            "missing_test_command": missing_test_command,
        }
        write_evidence(workspace, config, "behavior_diff.json", evidence)

        if violations:
            detail = "；".join(
                f"[{v['category']}] "
                + (f"文件 {v['path']} " if v.get("source") == "deliverable" else f"工具 {v.get('tool')} ")
                + f"命中 {v['pattern']}"
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
