"""防线 1：需求模板校验（阶段 0 完成后的首个推进点 1 -> 2，v0.52.0）。

自由文本需求是“隐性约束篡改”的源头：Agent 可以在理解隐含条件时做出利于
自己的解释（如把「实现登录」自行省略为不做锁定 / 限流）。本防线要求需求以
模板结构化——目标 / 禁止行为清单 / 输出接口定义 / 验收判定标准四个必填区块，
并把结构化结果写入状态机证据，作为后续防线（双模型复核、形式化约束、行为审计）
的统一比对基准。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..config import GateConfig, RequirementTemplateOptions
from ._base import DefenseCheckResult, DefenseLine
from ._common import load_yaml_or_json

REQUIREMENT_SECTIONS = ("goal", "forbidden", "interfaces", "acceptance")

SECTION_LABELS = {
    "goal": "目标",
    "forbidden": "禁止行为清单",
    "interfaces": "输出接口定义",
    "acceptance": "验收判定标准",
}

# 命令行 / init-requirement 使用的默认模板骨架（注释保留给人工填写）
TEMPLATE_SKELETON = """\
# phase-barrier 需求模板（防线 1）
# 填写后由校验器在阶段 0 -> 1 时检查；缺失区块会被逐条列出。
goal: ""  # 不超过 {goal_max_chars} 字的一句话目标
forbidden:
  # 至少 {min_forbidden} 条：逐条枚举所有不允许的行为
  - ""
interfaces:
  # 至少 {min_interfaces} 条：具体的输入输出签名
  - ""
  - ""
acceptance:
  # 至少 {min_acceptance} 条：可客观判断的通过条件
  - ""
  - ""
"""


def build_requirement(
    goal: str,
    forbidden: list[str],
    interfaces: list[str],
    acceptance: list[str],
) -> dict[str, Any]:
    """构造标准模板字典（供 CLI / 测试复用）。"""
    return {
        "goal": goal,
        "forbidden": list(forbidden),
        "interfaces": list(interfaces),
        "acceptance": list(acceptance),
    }


def _normalize_item(value: Any) -> str:
    """去掉空白后用于「重复条目」判定。"""
    return re.sub(r"\\s+", "", str(value if value is not None else ""))


def _list_section_issues(
    items: Any,
    label: str,
    min_items: int,
    options: RequirementTemplateOptions,
) -> list[str]:
    """列表区块（禁止行为 / 接口 / 验收）的通用校验：条数 + 具体性。

    v0.57.0 新增两项，用于拦截「模板填充型」逃逸（字段齐全、条目凑数、语义为空）：

    - 重复条目：同一条目重复填写只是凑够条数，没有信息量；
    - 空话短语：条目命中 ``options.vague_phrases`` 时无法客观判定（如「标准接口」
      「功能正常运行」「不允许出现不合理的操作」）。
    """
    if not isinstance(items, list) or not items:
        return [f"缺失或为空：{label}（至少 {min_items} 条）"]
    issues: list[str] = []
    if len(items) < min_items:
        issues.append(f"{label} 条目数 {len(items)} < 最低要求 {min_items}")

    bad_type = [
        idx + 1
        for idx, value in enumerate(items)
        if not isinstance(value, str) or not value.strip()
    ]
    if bad_type:
        issues.append(
            f"{label}第 {'、'.join(str(i) for i in bad_type[:3])} 条不是非空字符串"
            "（若条目含 `: `，YAML 会把它解析成映射，请给条目加引号）"
        )

    normalized = [_normalize_item(x) for x in items]
    if options.reject_duplicate_items:
        seen: set[str] = set()
        dups: list[str] = []
        for value in normalized:
            if value in seen and value not in dups:
                dups.append(value)
            seen.add(value)
        if dups:
            issues.append(f"{label}存在重复条目（只是凑够条数）：{'、'.join(dups[:3])}")

    phrases = [str(x) for x in (options.vague_phrases or []) if str(x).strip()]
    if phrases:
        hits: list[str] = []
        for raw, value in zip(items, normalized):
            for phrase in phrases:
                if phrase in value:
                    hits.append(f"{raw}（含空话短语「{phrase}」）")
                    break
        if hits:
            issues.append(f"{label}内容为空话、无法客观判定：{'；'.join(hits[:3])}")
    return issues


def validate_requirement(
    data: dict[str, Any], options: RequirementTemplateOptions
) -> tuple[bool, list[str], dict[str, Any]]:
    """校验模板内容，返回 ``(ok, issues, stats)``。

    - 区块缺失 / 类型错误 -> issue；
    - 目标超长 -> issue；
    - 禁止行为 / 接口 / 验收条目数低于阈值 -> issue。
    """
    issues: list[str] = []
    stats: dict[str, Any] = {}

    goal = data.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        issues.append(f"缺失或为空：{SECTION_LABELS['goal']}")
    else:
        goal = goal.strip()
        if len(goal) > options.goal_max_chars:
            issues.append(
                f"{SECTION_LABELS['goal']} 超过 {options.goal_max_chars} 字"
                f"（当前 {len(goal)} 字）"
            )
    stats["goal_len"] = len(goal) if isinstance(goal, str) else 0

    forbidden = data.get("forbidden")
    issues.extend(
        _list_section_issues(
            forbidden,
            SECTION_LABELS["forbidden"],
            options.min_forbidden_items,
            options,
        )
    )
    stats["forbidden_count"] = len(forbidden) if isinstance(forbidden, list) else 0

    interfaces = data.get("interfaces")
    issues.extend(
        _list_section_issues(
            interfaces,
            SECTION_LABELS["interfaces"],
            options.min_interface_items,
            options,
        )
    )
    stats["interface_count"] = len(interfaces) if isinstance(interfaces, list) else 0

    acceptance = data.get("acceptance")
    issues.extend(
        _list_section_issues(
            acceptance,
            SECTION_LABELS["acceptance"],
            options.min_acceptance_items,
            options,
        )
    )
    stats["acceptance_count"] = len(acceptance) if isinstance(acceptance, list) else 0

    stats["sections"] = {
        "goal": bool(goal),
        "forbidden": isinstance(forbidden, list) and len(forbidden) >= options.min_forbidden_items,
        "interfaces": isinstance(interfaces, list) and len(interfaces) >= options.min_interface_items,
        "acceptance": isinstance(acceptance, list) and len(acceptance) >= options.min_acceptance_items,
    }
    return (not issues, issues, stats)


def dump_template_skeleton(options: RequirementTemplateOptions) -> str:
    return TEMPLATE_SKELETON.format(
        goal_max_chars=options.goal_max_chars,
        min_forbidden=options.min_forbidden_items,
        min_interfaces=options.min_interface_items,
        min_acceptance=options.min_acceptance_items,
    )


class RequirementTemplateLine(DefenseLine):
    """防线 1 实现：首个推进点（1 -> 2）校验需求模板。

    状态机在 bootstrap 时自动把阶段 0（需求接收）记入历史并进入阶段 1
    （Spec 设计），因此“拒绝自由文本需求进入设计”发生在第一次推进
    （spec 完成后 advance 到阶段 2）之前——此时 spec 尚未被接受。
    """

    name = "requirement_template"
    trigger = ((1, 2),)

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        options: RequirementTemplateOptions = config.defense.requirement_template
        tmpl_path = workspace / options.template_file
        data = load_yaml_or_json(tmpl_path)
        # 兼容 CLI init-requirement 已写入状态机的模板（写入 .agent_gate 更安全）
        if data is None:
            data = state.get_evidence("requirement_template")

        provided_from = "file" if tmpl_path.is_file() else "state"
        if data is None:
            if options.strict:
                missing = "、".join(SECTION_LABELS[s] for s in REQUIREMENT_SECTIONS)
                return DefenseCheckResult(
                    False,
                    "防线 1（需求模板）未通过：工作区缺少需求模板 "
                    f"{options.template_file}，无法进入阶段 1。请运行 "
                    f"`python -m anti_shortcut init-requirement` 按模板填写需求。"
                    f"缺失区块：{missing}",
                    {"strict": True, "template_file": str(tmpl_path), "missing": missing},
                )
            return DefenseCheckResult(
                True,
                "防线 1（需求模板）：未提供结构化需求模板（strict=false），"
                "按自由文本需求放行。建议运行 `python -m anti_shortcut init-requirement` "
                "填写模板以启用后续防线",
                {"strict": False, "template_file": str(tmpl_path), "provided": False},
            )

        ok, issues, stats = validate_requirement(data, options)
        evidence: dict[str, Any] = {
            "template_file": str(tmpl_path) if tmpl_path.is_file() else "state",
            "provided_from": provided_from,
            "strict": options.strict,
            "issues": issues,
            "stats": stats,
        }
        if not ok:
            return DefenseCheckResult(
                False,
                "防线 1（需求模板）未通过：" + "；".join(issues),
                evidence,
            )
        # 通过：把结构化模板写入状态证据，供后续防线（双模型 / 形式化 / 审计）引用
        try:
            state.set_evidence("requirement_template", data)
        except Exception:
            pass
        return DefenseCheckResult(
            True,
            "防线 1（需求模板）通过：四个必填区块齐备，模板已存入状态证据",
            evidence,
        )


def format_template_yaml(data: dict[str, Any]) -> str:
    """把模板字典格式化为 YAML（供 CLI 输出）。"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
