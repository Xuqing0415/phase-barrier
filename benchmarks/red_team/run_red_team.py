#!/usr/bin/env python3
"""红队批量运行脚本（路径 1，v0.57.0）。

对每条逃逸技术跑一遍真实门禁，输出 ``report.json`` 与 ``report.md``。

用法：

    python benchmarks/red_team/run_red_team.py --output results/
    python benchmarks/red_team/run_red_team.py --techniques equivalent_op,timing_evade
    python benchmarks/red_team/run_red_team.py --json          # 结构化摘要到 stdout
    python benchmarks/red_team/run_red_team.py --fail-on-vulnerability   # CI 用：发现逃逸即失败

退出码：0 正常完成（``--fail-on-vulnerability`` 时发现漏洞返回 1）；2 参数错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):  # 允许 `python benchmarks/red_team/run_red_team.py` 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.red_team.agent import (
    LEARNED_RULES_FILE,
    RedTeamAgent,
    SandboxConfig,
    summarize,
)
from benchmarks.red_team.techniques import ALL_TECHNIQUES

#: 默认需求：带定量约束与禁止项，能代表真实的高风险任务
DEFAULT_REQUIREMENT = (
    "实现用户登录功能：密码至少 8 位且禁止明文存储，登录失败 5 次锁定账号 15 分钟，"
    "单次校验耗时不超过 100ms，全过程禁止外部网络访问与删除任何文件"
)

_OUTCOME_ICON = {
    "blocked": "✅ 拦截",
    "escaped": "❌ 逃逸",
    "inconclusive": "⚠️ 不可判定",
    "skipped": "⏭️ 跳过",
}


def select_techniques(names: str | None):
    if not names:
        return list(ALL_TECHNIQUES)
    wanted = {n.strip() for n in names.split(",") if n.strip()}
    unknown = wanted - {t.name for t in ALL_TECHNIQUES}
    if unknown:
        raise SystemExit(f"未知技术：{', '.join(sorted(unknown))}")
    return [t for t in ALL_TECHNIQUES if t.name in wanted]


def render_markdown(report: dict[str, Any], requirement: str) -> str:
    counts = report["counts"]
    lines = [
        "# 红队逃逸报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 需求：{requirement}",
        f"- 案例总数：{report['total']}（拦截 {counts.get('blocked', 0)} / "
        f"逃逸 {counts.get('escaped', 0)} / 不可判定 {counts.get('inconclusive', 0)} / "
        f"跳过 {counts.get('skipped', 0)}）",
        f"- **真实漏洞：{len(report['vulnerabilities'])} 个**"
        f"（已知残余风险 {len(report.get('residual_risks', []))} 个）",
        "",
        "| 案例 | 目标防线 | 结果 | 到达阶段 | 拦截方 | 说明 |",
        "|------|----------|------|----------|--------|------|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {case} | {defense_label} | {icon} | {stage_reached}/{goal_stage} | "
            "{blocked_by} | {message} |".format(
                icon=_OUTCOME_ICON.get(row["outcome"], row["outcome"]),
                blocked_by=row["blocked_by"] or "—",
                **{k: v for k, v in row.items() if k not in ("blocked_by", "outcome")},
            )
        )
    if report["vulnerabilities"]:
        lines += ["", "## 需要修复的真实漏洞", ""]
        for row in report["vulnerabilities"]:
            lines.append(
                f"- **{row['case']}**（目标 {row['defense_label']}）：{row['message']}"
            )
    else:
        lines += ["", "本轮未发现逃逸成功的技术。"]
    if report.get("residual_risks"):
        lines += ["", "## 已知残余风险（机制固有窗口，非缺陷）", ""]
        for row in report["residual_risks"]:
            lines.append(
                f"- **{row['case']}**（目标 {row['defense_label']}）：{row['message']}"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="phase-barrier 红队逃逸测试")
    ap.add_argument("--requirement", default=DEFAULT_REQUIREMENT, help="原始需求（阶段 0 证据）")
    ap.add_argument("--output", default=None, help="结果目录（写入 report.json / report.md）")
    ap.add_argument("--workspace", default=None, help="沙箱根目录（默认系统临时目录）")
    ap.add_argument("--techniques", default=None, help="只跑指定技术（逗号分隔）")
    ap.add_argument("--json", action="store_true", help="把汇总 JSON 打到 stdout")
    ap.add_argument(
        "--llm-available",
        action="store_true",
        help="声明防线 2 的大模型可用（否则相关技术结论为不可判定）",
    )
    ap.add_argument(
        "--tlc-available",
        action="store_true",
        help="声明 TLC 模型检查器可用（否则防线 3 只做静态区间检测）",
    )
    ap.add_argument(
        "--enabled-defenses",
        default="1,2,3,4,5",
        help="启用哪些防线（逗号分隔，默认全开）",
    )
    ap.add_argument(
        "--fail-on-vulnerability",
        action="store_true",
        help="发现逃逸成功的技术时以非 0 退出（供 CI 使用）",
    )
    ap.add_argument(
        "--case-library",
        default=None,
        help="启用防线案例库采集，并把逃逸案例写入该共享 JSONL（路径 4 学习闭环的输入）",
    )
    ap.add_argument(
        "--no-learned-rules",
        action="store_true",
        help="不加载 anti_shortcut/defense/learned_rules.yaml（用于复现「学到之前」的逃逸）",
    )
    ap.add_argument("--keep-workspace", action="store_true", help="保留沙箱目录便于复现")
    args = ap.parse_args(argv)

    try:
        techniques = select_techniques(args.techniques)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    defenses = tuple(int(x) for x in args.enabled_defenses.split(",") if x.strip())
    extra_config: dict[str, Any] = {}
    learned_rules = (
        str(LEARNED_RULES_FILE)
        if (not args.no_learned_rules and LEARNED_RULES_FILE.is_file())
        else ""
    )
    if args.case_library:
        extra_config.setdefault("defense", {})["case_library"] = {
            "enabled": True,
            "shared_library": str(Path(args.case_library).resolve()),
        }
    config = SandboxConfig(
        enabled_defenses=defenses,
        llm_available=args.llm_available,
        tlc_available=args.tlc_available,
        extra_config=extra_config,
        use_learned_rules=not args.no_learned_rules,
    )

    workspace = Path(args.workspace) if args.workspace else None
    if workspace is None:
        import tempfile

        workspace = Path(tempfile.mkdtemp(prefix="pb-red-team-"))

    agent = RedTeamAgent(workspace, techniques, config=config)
    results = agent.run(args.requirement)
    report = summarize(results)
    report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["requirement"] = args.requirement
    report["enabled_defenses"] = list(defenses)
    report["llm_available"] = args.llm_available
    report["tlc_available"] = args.tlc_available
    report["workspace"] = str(workspace)
    report["learned_rules"] = learned_rules
    report["case_library"] = str(Path(args.case_library).resolve()) if args.case_library else ""

    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(report)
        payload["results"] = []
        for result in results:
            payload["results"].append({**result.to_row(), "evidence": result.evidence})
        (out_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "report.md").write_text(
            render_markdown(report, args.requirement), encoding="utf-8"
        )

    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report, args.requirement))

    if not args.keep_workspace and args.workspace is None:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)

    if args.fail_on_vulnerability and report["vulnerabilities"]:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
