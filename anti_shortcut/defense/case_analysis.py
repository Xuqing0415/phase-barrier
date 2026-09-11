"""案例分析与规则更新（路径 4，v0.59.0）。

从拦截案例库里**半自动**提取新逃逸模式，产出可供人工评审的规则 / 提示词更新建议：

- :func:`analyze_cases`：按（防线, 触发类型）分组，提取候选模式；对「已覆盖」的
  候选做减法，只留下**现有规则库还没有**的新模式；
- :func:`build_suggestions`：把发现整理成结构化建议（可直接落 YAML）；
- :func:`apply_suggestions`：把建议合并进目标规则文件（去重 + 正则编译校验 +
  「建议规则必须真能命中它来自的案例」反查），返回合并统计；
- :func:`append_few_shot`：把真实案例作为 few-shot 追加进防线 2 的提示词，
  以标记块方式重写，保证多次运行幂等。

设计红线：本模块**只产出建议**，绝不自动改运行时行为；合入必须由人评审后
以 PR 形式提交（``scripts/update_rules.py --apply`` 也只是写目标文件）。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .case_library import case_texts
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

__all__ = [
    "CANDIDATE_WEAKENING_WORDS",
    "analyze_cases",
    "build_suggestions",
    "render_markdown",
    "case_texts",
    "apply_suggestions",
    "append_few_shot",
    "extract_candidates",
]

#: 防线 2B 篡改核查里值得关注的弱化词（候选池；命中且不在现有清单中即建议新增）
CANDIDATE_WEAKENING_WORDS = (
    "尽量", "适当", "足够", "大致", "基本", "合理", "必要", "充分", "酌情",
    "尽可能", "一般来说", "通常", "大概", "或许", "可以", "应当考虑",
    "较为", "相对", "适度", "放宽", "简化", "忽略", "暂时不", "后续再",
)

#: 已经在防线 2 提示词里明确的弱化词（避免重复建议）
KNOWN_WEAKENING_WORDS = ("足够", "尽量", "适当")

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*)")
_SHELL_VERBS = (
    "rm", "rmdir", "unlink", "del", "truncate", "shred", "wipe",
    "remove-item", "clear-content", "git rm", "git clean", "find", "rsync",
    "scp", "ssh", "curl", "wget", "nc", "socat", "dd",
)
_QUOTED_RE = re.compile(r"[「『\"\x27]([^」』\"\x27]{2,40})[」』\"\x27]")
_VAR_RE = re.compile(r"变量\s+([A-Za-z_][A-Za-z0-9_]*)")

_MARKER = "<!-- learned-cases:start -->"
_MARKER_END = "<!-- learned-cases:end -->"


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


#: 命中的「短/长选项」——用于把 ``truncate -s`` 这种「动词 + 选项」提成更精确的规则
_FLAG_RE = re.compile(r"(?<![\w-])--?[A-Za-z][A-Za-z-]*\b")

#: 文件名后缀：``notes.txt`` 这类 token 是路径不是 API，提成规则没有价值（且过于具体）
_FILE_SUFFIXES = frozenset(
    {
        "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "java", "kt", "go",
        "rs", "rb", "php", "cs", "swift", "dart", "scala", "sh", "ps1", "bat",
        "sql", "c", "h", "cc", "cpp", "hpp", "lua", "pl", "r", "json", "yaml", "yml",
        "toml", "ini", "cfg", "conf", "txt", "md", "log", "db", "sqlite", "csv",
    }
)


def _looks_like_filename(token: str) -> bool:
    return "." in token and token.rsplit(".", 1)[-1].lower() in _FILE_SUFFIXES


def _shell_specific(verb: str, text: str) -> tuple[str, str] | None:
    """提取「动词 + 选项」形式（如 ``truncate -s``）。

    裸动词规则（``\btruncate\b``）太宽，容易误伤；只有当动词后跟了选项时才用它，
    否则退回裸动词。选项与动词之间允许任意参数（``[^\n;&|]{0,40}?``），
    保证生成的正则能反查命中原始命令文本。
    """
    for match in re.finditer(r"(?i)\b" + re.escape(verb) + r"\b([^\n;&|]{0,80})", text):
        # 取**最后一个**选项：破坏性选项通常写在最后（find ... -delete / rsync -a --delete）
        flags = _FLAG_RE.findall(match.group(1))
        if not flags:
            continue
        flag = flags[-1]
        token = f"{verb} {flag}"
        pattern = (
            r"(?i)\b"
            + re.escape(verb)
            + r"\s+[^\n;&|]{0,40}?"
            + re.escape(flag)
            + r"\b"
        )
        return token, pattern
    return None


def extract_candidates(text: str) -> list[dict[str, str]]:
    """从一段攻击载荷文本里提取候选模式（供人工评审，宁多勿漏）。"""
    out: list[dict[str, str]] = []
    for verb in _SHELL_VERBS:
        # 用词边界判断存在性：避免 "truncate" 里的 "nc"、"delete" 里的 "del" 被当成动词
        if re.search(r"(?i)\b" + re.escape(verb) + r"\b", text):
            specific = _shell_specific(verb, text)
            if specific is not None:
                out.append({"kind": "shell", "token": specific[0], "pattern": specific[1]})
                continue
            pattern = r"(?i)\b" + re.escape(verb).replace(r"\ ", r"\s+") + r"\b"
            out.append({"kind": "shell", "token": verb, "pattern": pattern})
    for match in _IDENT_RE.finditer(text):
        dotted = match.group(1)
        parts = dotted.split(".")
        token = ".".join(parts[:2]) if len(parts) > 1 else dotted
        if _looks_like_filename(token):
            continue
        out.append(
            {
                "kind": "identifier",
                "token": token,
                "pattern": r"(?i)\b" + re.escape(token).replace(r"\.", r"\.") + r"\b",
            }
        )
    return out


def _is_covered(token: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, token, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _categories_of(cases: Sequence[dict[str, Any]]) -> list[str]:
    """从案例里推断防线 4 的禁止类别（逃逸案例写在 ``context.category``，
    被拦下的案例写在 ``context.violations[].category``）。"""
    out: list[str] = []
    for case in cases:
        context = case.get("context") or {}
        if not isinstance(context, dict):
            continue
        if context.get("category"):
            out.append(str(context["category"]))
        for violation in context.get("violations") or []:
            if isinstance(violation, dict) and violation.get("category"):
                out.append(str(violation["category"]))
    return _dedupe(out)


def _case_texts(case: dict[str, Any]) -> list[str]:
    """案例里可用于「反查」的文本（实现见 :func:`case_library.case_texts`）。"""
    return case_texts(case)


def analyze_cases(
    cases: Sequence[dict[str, Any]],
    *,
    builtin_patterns: dict[str, list[str]] | None = None,
    min_count: int = 1,
) -> dict[str, Any]:
    """按（防线, 触发类型）分组分析案例，提取**新**模式。

    :param builtin_patterns: 现有规则库（类别 -> 正则列表），用于做减法；
        默认取 ``behavior_audit.BUILTIN_FORBIDDEN``。
    """
    if builtin_patterns is None:
        from .behavior_audit import BUILTIN_FORBIDDEN

        builtin_patterns = {
            category: list(spec["patterns"]) for category, spec in BUILTIN_FORBIDDEN.items()
        }

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[
            (str(case.get("defense_line") or "unknown"), str(case.get("trigger_type") or "unknown"))
        ].append(case)

    findings: list[dict[str, Any]] = []
    for (line, trigger), items in sorted(grouped.items()):
        if len(items) < min_count:
            continue
        finding: dict[str, Any] = {
            "defense_line": line,
            "trigger_type": trigger,
            "count": len(items),
            "case_ids": [str(c.get("case_id") or "") for c in items][:20],
        }
        texts = _dedupe(t for case in items for t in _case_texts(case))
        reasons = _dedupe(str(case.get("blocked_reason") or "") for case in items)

        if (
            trigger.startswith("forbidden_op:")
            or trigger == "untraced_write"
            or trigger.startswith("escape:")
        ):
            if trigger.startswith("forbidden_op:"):
                categories = trigger.split(":", 1)[1].split(",")
            else:
                # 逃逸 / 绕过包装器的案例：类别从 context 里取。**没有类别就不猜**——
                # 猜错类别会生成风马牛不相及的规则（如把 requirement.yaml 当危险操作）。
                categories = _categories_of(items)
            if not categories:
                finding["new_patterns"] = []
                finding["skipped_reason"] = "案例未记录禁止类别，跳过规则提取（需人工判断）"
                finding["sample_reason"] = reasons[0] if reasons else ""
                findings.append(finding)
                continue
            patterns = [p for cat in categories for p in builtin_patterns.get(cat, [])]
            candidates: list[dict[str, str]] = []
            for text in texts:
                for candidate in extract_candidates(text):
                    if not _is_covered(candidate["token"], patterns):
                        candidate["evidence"] = text[:160]
                        candidate["category"] = categories[0]
                        candidates.append(candidate)
            finding["new_patterns"] = _dedupe(
                json.dumps(c, ensure_ascii=False, sort_keys=True) for c in candidates
            )
            finding["new_patterns"] = [json.loads(x) for x in finding["new_patterns"]]
        elif trigger == "vague_template":
            phrases = _dedupe(
                match
                for reason in reasons
                for match in _QUOTED_RE.findall(reason)
            )
            finding["vague_phrases"] = phrases
        elif trigger in ("static_contradiction", "alias_contradiction"):
            finding["variables"] = _dedupe(
                match for reason in reasons for match in _VAR_RE.findall(reason)
            )
        elif trigger == "semantic_tamper":
            finding["weakening_words"] = [
                word
                for word in CANDIDATE_WEAKENING_WORDS
                if word not in KNOWN_WEAKENING_WORDS
                and any(word in text for text in texts)
            ]
        finding["sample_reason"] = reasons[0] if reasons else ""
        findings.append(finding)

    total = sum(f["count"] for f in findings)
    return {
        "total_cases": len(cases),
        "grouped_cases": total,
        "groups": findings,
        "findings_count": len(findings),
    }


def build_suggestions(report: dict[str, Any]) -> dict[str, Any]:
    """把分析报告整理成结构化更新建议（可直接落 YAML）。"""
    forbidden: dict[str, list[str]] = defaultdict(list)
    vague: list[str] = []
    weakening: list[str] = []
    examples: list[dict[str, str]] = []
    for group in report.get("groups", []):
        for item in group.get("new_patterns", []):
            category = str(item.get("category") or "file_delete")
            forbidden[category].append(str(item["pattern"]))
        vague.extend(group.get("vague_phrases", []))
        weakening.extend(group.get("weakening_words", []))
        if group.get("sample_reason"):
            examples.append(
                {
                    "defense_line": str(group["defense_line"]),
                    "trigger_type": str(group["trigger_type"]),
                    "reason": str(group["sample_reason"])[:300],
                }
            )
    return {
        "forbidden_patterns": {k: _dedupe(v) for k, v in sorted(forbidden.items())},
        "vague_phrases": _dedupe(vague),
        "weakening_words": _dedupe(weakening),
        "few_shot_examples": examples[:5],
        "note": "本文件由案例库分析生成，必须人工审核后以 PR 形式合入；不要自动上线。",
    }


def render_markdown(report: dict[str, Any]) -> str:
    """渲染可读的分析报告。"""
    lines = [
        "# 拦截案例分析报告",
        "",
        f"- 案例总数：{report.get('total_cases', 0)}",
        f"- 分组数：{report.get('findings_count', 0)}",
        "",
        "| 防线 | 触发类型 | 案例数 | 新发现 |",
        "|------|----------|--------|--------|",
    ]
    for group in report.get("groups", []):
        new = []
        if group.get("new_patterns"):
            new.append(f"新规则候选 {len(group['new_patterns'])}")
        if group.get("vague_phrases"):
            new.append(f"空话短语 {len(group['vague_phrases'])}")
        if group.get("weakening_words"):
            new.append(f"弱化词 {len(group['weakening_words'])}")
        if group.get("variables"):
            new.append(f"变量 {len(group['variables'])}")
        lines.append(
            "| {line} | {trigger} | {count} | {new} |".format(
                line=group["defense_line"],
                trigger=group["trigger_type"],
                count=group["count"],
                new="、".join(new) or "—",
            )
        )
    for group in report.get("groups", []):
        if not group.get("new_patterns"):
            continue
        lines += ["", f"## {group['defense_line']} / {group['trigger_type']}", ""]
        for item in group["new_patterns"]:
            lines.append(
                f"- `{item['token']}` -> `{item['pattern']}`（来自：`{item['evidence'][:80]}`）"
            )
    lines.append("")
    return "\n".join(lines)


def apply_suggestions(
    suggestions: dict[str, Any],
    dest: str | Path,
    *,
    verify_texts: Sequence[str] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """把建议合并进目标 YAML 规则文件。

    - 合并前做正则编译校验，非法正则直接丢弃并记录；
    - 给了 ``verify_texts`` 时做**反查**：建议的正则必须至少命中一条它来自的
      案例文本，否则丢弃（防止把「看起来像」的规则写进规则库）；
    - 幂等：已在目标文件里的条目不会重复写入；
    - ``write=False`` 时只做校验与统计（dry-run），不落盘。
    """
    dest = Path(dest)
    existing: dict[str, Any] = {}
    if dest.is_file():
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            existing = loaded

    stats: dict[str, Any] = {"added": {}, "skipped": [], "dest": str(dest)}
    forbidden = {
        str(k): list(v or []) for k, v in (existing.get("forbidden_patterns") or {}).items()
    }
    for category, patterns in (suggestions.get("forbidden_patterns") or {}).items():
        target = forbidden.setdefault(str(category), [])
        for pattern in patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                stats["skipped"].append(f"非法正则 {pattern!r}: {exc}")
                continue
            if verify_texts is not None and not any(
                compiled.search(text) for text in verify_texts
            ):
                stats["skipped"].append(f"反查未命中任何案例，丢弃 {pattern!r}")
                continue
            if pattern in target:
                continue
            target.append(pattern)
            stats["added"].setdefault(str(category), []).append(pattern)

    vague = _dedupe(list(existing.get("vague_phrases") or []) + list(suggestions.get("vague_phrases") or []))
    weakening = _dedupe(
        list(existing.get("weakening_words") or []) + list(suggestions.get("weakening_words") or [])
    )
    examples = list(existing.get("few_shot_examples") or []) + list(
        suggestions.get("few_shot_examples") or []
    )
    payload = {
        "note": suggestions.get("note")
        or existing.get("note")
        or "由案例库分析生成，人工审核后合入。",
        "forbidden_patterns": {k: v for k, v in sorted(forbidden.items()) if v},
        "vague_phrases": vague,
        "weakening_words": weakening,
        "few_shot_examples": examples[-5:],
    }
    if write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    stats["written"] = bool(write)
    stats["total_patterns"] = sum(len(v) for v in payload["forbidden_patterns"].values())
    return stats


def append_few_shot(
    prompt_path: str | Path,
    examples: Sequence[dict[str, Any]],
    *,
    start_marker: str = _MARKER,
    end_marker: str = _MARKER_END,
) -> int:
    """把案例作为 few-shot 追加进提示词（标记块整体重写，多次运行幂等）。"""
    path = Path(prompt_path)
    if not path.is_file() or not examples:
        return 0
    text = path.read_text(encoding="utf-8")
    block_lines = [start_marker, "## 真实拦截案例（自动追加，人工审核后生效）", ""]
    for index, example in enumerate(examples, start=1):
        block_lines.append(
            f"{index}. [{example.get('defense_line')}/{example.get('trigger_type')}] "
            f"{example.get('reason')}"
        )
    block_lines.append(end_marker)
    block = "\n".join(block_lines)

    if start_marker in text and end_marker in text:
        head = text.split(start_marker)[0].rstrip()
        tail = text.split(end_marker, 1)[1]
        text = f"{head}\n\n{block}{tail}"
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")
    return len(examples)
