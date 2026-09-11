"""拦截案例库分析脚本（路径 4，v0.59.0）。

读取一个或多个 JSONL 案例库（``defense.case_library`` 落盘产物），按
（防线, 触发类型）分组提取**现有规则库尚未覆盖**的新模式，输出：

- ``--report``：分析报告 JSON（机器可读，供 ``update_rules.py`` 消费）；
- ``--markdown``：同内容的人读报告；
- ``--suggestions``：结构化规则更新建议 JSON（**必须人工审核后**再 apply）。

用法::

    python scripts/analyze_cases.py --cases benchmarks/red_team/results/learning_loop/case_library.jsonl \
        --report analysis_report.json --markdown analysis_report.md --suggestions suggestions.json
    python scripts/analyze_cases.py --workspace . --json      # 读 <workspace>/.agent_gate/defense 下的案例库

退出码：0 = 分析完成（可能含新建议）；1 = 案例库为空 / 无法读取。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python scripts/analyze_cases.py` 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _workspace_libraries(workspaces: list[str]) -> list[Path]:
    from anti_shortcut.config import GateConfig

    gate_dir = GateConfig().gate_dir_name
    out: list[Path] = []
    for raw in workspaces:
        candidate = Path(raw) / gate_dir / "defense" / "case_library.jsonl"
        if candidate.is_file():
            out.append(candidate)
    return out


def main(argv: list[str] | None = None) -> int:
    from anti_shortcut.defense.case_analysis import (
        analyze_cases,
        build_suggestions,
        render_markdown,
    )
    from anti_shortcut.defense.case_library import load_cases, summarize_cases

    ap = argparse.ArgumentParser(description="phase-barrier 拦截案例分析（路径 4）")
    ap.add_argument("--cases", nargs="*", default=[], help="JSONL 案例库文件或目录（可多个）")
    ap.add_argument("--workspace", action="append", default=[], help="工作区目录（读其 .agent_gate/defense 下的案例库）")
    ap.add_argument("--report", default=None, help="分析报告 JSON 输出路径")
    ap.add_argument("--markdown", default=None, help="分析报告 Markdown 输出路径")
    ap.add_argument("--suggestions", default=None, help="规则更新建议 JSON 输出路径")
    ap.add_argument("--min-count", type=int, default=1, help="同一（防线, 触发类型）至少出现几次才分析")
    ap.add_argument("--json", action="store_true", help="把报告 JSON 打到 stdout")
    ap.add_argument(
        "--fail-on-new-patterns",
        action="store_true",
        help="提取到新规则建议时以非 0 退出（供「必须人工审核」的 CI 门禁使用）",
    )
    args = ap.parse_args(argv)

    sources = [Path(p) for p in args.cases]
    sources += _workspace_libraries(args.workspace)
    if not sources:
        print("未指定任何案例库（--cases 或 --workspace）", file=sys.stderr)
        return 1

    cases = load_cases(sources)
    if not cases:
        print(f"案例库为空：{', '.join(str(s) for s in sources)}", file=sys.stderr)
        return 1

    report = analyze_cases(cases, min_count=args.min_count)
    report["summary"] = summarize_cases(cases)
    report["sources"] = [str(s) for s in sources]
    suggestions = build_suggestions(report)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
    if args.suggestions:
        Path(args.suggestions).parent.mkdir(parents=True, exist_ok=True)
        Path(args.suggestions).write_text(
            json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    new_patterns = sum(len(v) for v in suggestions["forbidden_patterns"].values())
    new_patterns += len(suggestions["vague_phrases"]) + len(suggestions["weakening_words"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
        print(
            f"\n共分析 {report['total_cases']} 条案例，"
            f"分组 {report['findings_count']} 个，新规则建议 {new_patterns} 条。"
        )
        if new_patterns:
            print("下一步：人工审核 suggestions.json，再用 scripts/update_rules.py --apply 合入。")
    return 1 if (args.fail_on_new_patterns and new_patterns) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
