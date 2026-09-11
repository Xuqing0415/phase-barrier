"""规则 / 提示词更新脚本（路径 4，v0.59.0）。

消费 ``scripts/analyze_cases.py`` 产出的建议 JSON，把**经过校验**的新规则合入目标
规则文件；缺省为 dry-run（只校验 + 统计，不落盘），``--apply`` 才真正写入。

安全约束（刻意设计，不可绕过）：

- 合入前逐条做**正则编译校验**；非法正则直接丢弃；
- 给了 ``--verify-cases`` 时逐条做**反查**：建议正则必须至少命中一条它来自的案例
  文本，否则丢弃（避免把「看起来像」的规则写进规则库）；
- ``--apply`` 且没有任何反查案例时**拒绝执行**（除非显式 ``--allow-unverified``）；
- 幂等：重复运行不会重复写入。

用法::

    # dry-run：先看会加什么
    python scripts/update_rules.py --suggestions suggestions.json \
        --dest anti_shortcut/defense/learned_rules.yaml --verify-cases case_library.jsonl
    # 人工审核通过后落盘
    python scripts/update_rules.py --suggestions suggestions.json \
        --dest anti_shortcut/defense/learned_rules.yaml --verify-cases case_library.jsonl --apply

退出码：0 = 成功（含 dry-run）；1 = 输入错误 / 建议文件不可读；2 = 缺少反查案例。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python scripts/update_rules.py` 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_suggestions(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"建议文件顶层必须是映射：{path}")
    return data


def main(argv: list[str] | None = None) -> int:
    from anti_shortcut.defense.case_analysis import apply_suggestions, case_texts
    from anti_shortcut.defense.case_library import load_cases

    ap = argparse.ArgumentParser(description="phase-barrier 规则更新（路径 4，人工审核后合入）")
    ap.add_argument("--suggestions", required=True, help="analyze_cases.py 产出的建议 JSON/YAML")
    ap.add_argument("--dest", required=True, help="目标规则文件（如 anti_shortcut/defense/learned_rules.yaml）")
    ap.add_argument("--verify-cases", nargs="*", default=None, help="反查用的案例库（JSONL/目录）")
    ap.add_argument("--allow-unverified", action="store_true", help="允许在没有反查案例的情况下 apply")
    ap.add_argument("--prompt", action="append", default=None, help="追加 few-shot 示例的提示词文件（可多次）")
    ap.add_argument("--apply", action="store_true", help="真正写入（缺省为 dry-run）")
    ap.add_argument("--json", action="store_true", help="把统计打到 stdout（JSON）")
    args = ap.parse_args(argv)

    src = Path(args.suggestions)
    if not src.is_file():
        print(f"建议文件不存在：{src}", file=sys.stderr)
        return 1
    try:
        suggestions = _load_suggestions(src)
    except (ValueError, OSError) as exc:
        print(f"建议文件无法解析：{exc}", file=sys.stderr)
        return 1

    verify_texts: list[str] | None = None
    if args.verify_cases:
        cases = load_cases(args.verify_cases)
        if not cases:
            print(f"反查案例为空：{', '.join(args.verify_cases)}", file=sys.stderr)
            return 1
        verify_texts = [text for case in cases for text in case_texts(case)]
    if args.apply and verify_texts is None and not args.allow_unverified:
        print(
            "拒绝执行：--apply 需要 --verify-cases 做反查（或显式 --allow-unverified 承担风险）",
            file=sys.stderr,
        )
        return 2

    stats = apply_suggestions(
        suggestions, args.dest, verify_texts=verify_texts, write=args.apply
    )
    stats["prompts_updated"] = {}
    if args.apply and args.prompt:
        from anti_shortcut.defense.case_analysis import append_few_shot

        examples = list(suggestions.get("few_shot_examples") or [])
        for prompt in args.prompt:
            stats["prompts_updated"][prompt] = append_few_shot(prompt, examples)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        mode = "已写入" if args.apply else "dry-run（未写入；加 --apply 落盘）"
        print(f"[{mode}] 目标：{stats['dest']}")
        for category, patterns in (stats["added"] or {}).items():
            for pattern in patterns:
                print(f"  + [{category}] {pattern}")
        if not stats["added"]:
            print("  （没有新增规则）")
        for skipped in stats["skipped"]:
            print(f"  - 丢弃：{skipped}")
        print(f"合计规则条数：{stats['total_patterns']}")
        if not args.apply:
            print("人工审核以上差异后再执行 --apply。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
