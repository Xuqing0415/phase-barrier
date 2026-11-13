"""Build the formal batch's tables from verified evidence, without pooling runs."""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROSS_INTERRUPTION_ELAPSED = {("matplotlib__matplotlib-24970", "baseline")}
spec = importlib.util.spec_from_file_location("report_checks", Path(__file__).with_name("verified20_checks.py"))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def read_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = [(row["instance_id"], row["mode"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate instance/mode in " + str(path))
    return rows


def pairs(rows):
    grouped = {}
    for row in rows:
        if row.get("note") == "graded" and str(row.get("resolved")) in ("0", "1"):
            grouped.setdefault(row["instance_id"], {})[row["mode"]] = row
    return {iid: pair for iid, pair in grouped.items() if set(pair) == {"baseline", "gated"}}


def outcomes(group):
    return {"pairs": len(group),
            "baseline": sum(int(p["baseline"]["resolved"]) for p in group.values()),
            "gated": sum(int(p["gated"]["resolved"]) for p in group.values()),
            "baseline_only": sum(p["baseline"]["resolved"] == "1" and p["gated"]["resolved"] == "0"
                                 for p in group.values()),
            "gated_only": sum(p["baseline"]["resolved"] == "0" and p["gated"]["resolved"] == "1"
                              for p in group.values())}


def consistency(before, after, expected):
    a, b = pairs(before), pairs(after)
    overlap = sorted(set(a) & set(expected))
    compared = sorted(set(overlap) & set(b))
    mismatch = [iid for iid in compared if any(a[iid][m]["resolved"] != b[iid][m]["resolved"]
                                             for m in ("baseline", "gated"))]
    return {"overlap": overlap, "compared": len(compared), "consistent": len(compared) - len(mismatch),
            "inconsistent": mismatch, "pending": sorted(set(overlap) - set(compared))}


def percentile(values, quantile):
    values = sorted(values)
    if not values:
        return None
    at = (len(values) - 1) * quantile
    low = int(at)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (at - low)


def elapsed_is_comparable(row):
    return (row["instance_id"], row["mode"]) not in CROSS_INTERRUPTION_ELAPSED


def cost_statistics(rows, metric):
    values = [float(row[metric]) for row in rows if row.get(metric) not in (None, "")]
    return {"n": len(values), "median": percentile(values, .5), "p90": percentile(values, .9)}


def elapsed_p90_tradeoffs(cost_sensitivity):
    result = []
    groups = sorted({v["group"] for v in cost_sensitivity})
    for group in groups:
        rows = {v["mode"]: v for v in cost_sensitivity
                if v["group"] == group and v["scope"] == "排除跨中断耗时"}
        b, g = rows.get("baseline", {}), rows.get("gated", {})
        bp, gp = b.get("p90"), g.get("p90")
        result.append({"group": group, "baseline_n": b.get("n", 0), "gated_n": g.get("n", 0),
                       "baseline_p90": bp, "gated_p90": gp,
                       "ratio": gp / bp if bp is not None and bp > 0 and gp is not None else None})
    return result


def tradeoff_text(summary):
    lines = []
    for value in summary["elapsed_p90_tradeoffs"]:
        if value["ratio"] is None:
            continue
        lines.append(f"{value['group']}：排除跨中断耗时后，gated P90 为 baseline 的 {value['ratio']:.2f}×"
                     f"（{value['gated_p90']:,.1f}s vs {value['baseline_p90']:,.1f}s；"
                     f"gated n={value['gated_n']}、baseline n={value['baseline_n']}）。")
    lines.append("这是两组 P90 的描述性比值，不是逐实例平均减速，也不能隔离门禁、测试和模型重试各自的耗时贡献。"
                 "部分结果不能证明总体解决率不降低；最终结论须同时报告配对解决率和成本。")
    lines.append("最终成本结论须基于全部 40 项核验证据，独立与复用分开；全量独立配对 n=18，"
                 "排除跨中断耗时后 baseline 时间 n=17、gated n=18。P90 对离群值敏感，"
                 "样本量有限；同一正式批的中途估计曾随样本增加由 2.16× 漂移至 1.87×，"
                 "这些中间数字及最终比值均为点估计，不是精确值或置信区间。")
    return lines


def summarize(formal, lite, cached, pilot, reuse_pilot, tasks, initial_graded=None):
    expected = {task["instance_id"] for task in tasks}
    complete = pairs(formal)
    groups = {"独立部分": {iid: p for iid, p in complete.items() if iid not in checks.REUSED_INSTANCES},
              "复用部分": {iid: p for iid, p in complete.items() if iid in checks.REUSED_INSTANCES}}
    costs, cost_sensitivity, costs_by_origin = [], [], []
    original_keys = {(r["instance_id"], r["mode"]) for r in initial_graded or [] if r["note"] == "graded"}
    def origin(row):
        if initial_graded is None:
            return "来源未核验"
        return "中断前已评分" if (row["instance_id"], row["mode"]) in original_keys else "恢复后评分（含补评）"
    for name, group in groups.items():
        selected = [row for pair in group.values() for row in pair.values()]
        for mode in ("baseline", "gated"):
            mode_rows = [row for row in selected if row["mode"] == mode]
            for key in ("turns", "seconds", "gate_intercepts", "diff_chars"):
                eligible = [r for r in mode_rows if key != "seconds" or elapsed_is_comparable(r)]
                costs.append({"group": name, "mode": mode, "metric": key,
                              "scope": "排除跨中断耗时" if key == "seconds" else "全部实例",
                              **cost_statistics(eligible, key)})
            for scope, rows in (("全部实例（原始墙钟）", mode_rows),
                                ("排除跨中断耗时", [r for r in mode_rows if elapsed_is_comparable(r)])):
                cost_sensitivity.append({"group": name, "mode": mode, "scope": scope,
                                         **cost_statistics(rows, "seconds")})
            for source in sorted({origin(r) for r in mode_rows}):
                source_rows = [r for r in mode_rows if origin(r) == source]
                for key in ("turns", "seconds", "gate_intercepts", "diff_chars"):
                    eligible = [r for r in source_rows if key != "seconds" or elapsed_is_comparable(r)]
                    costs_by_origin.append({"group": name, "mode": mode, "origin": source, "metric": key,
                                            **cost_statistics(eligible, key)})
    lite_ids, cached_ids = set(pairs(lite)), set(pairs(cached))
    old_overlap = lite_ids & cached_ids
    comparisons = {
        "链路 pilot-5 → 正式批": consistency(pilot, formal, expected),
        "前两批共有实例 → 正式批": consistency([r for r in lite if r["instance_id"] in old_overlap], formal, expected),
        "缓存-15 → 正式批（复用）": consistency(cached, formal, expected),
    }
    anomalies = []
    for row in formal:
        if row["note"] != "graded":
            continue
        tags = []
        if float(row.get("seconds") or 0) > 3600:
            tags.append("over_60_minutes")
        if not elapsed_is_comparable(row):
            tags.append("cross_interruption_wall_time_excluded_from_efficiency")
        classified = checks.classify_gate_result(row)
        tags.extend(classified["flags"])
        if classified["failures"]:
            tags.append("gate_complete_official_unresolved_requires_A_B_C_review")
        if row["mode"] == "gated" and int(row.get("gate_intercepts") or 0) == 0:
            tags.append("zero_interception_requires_benign_suspicious_review")
        if tags:
            anomalies.append({"instance_id": row["instance_id"], "mode": row["mode"], "tags": tags})
    reuse_history = []
    sources = [("缓存批 pilot", reuse_pilot), ("缓存-15", cached), ("正式批", formal)]
    for iid in checks.REUSED_INSTANCES:
        for mode in ("baseline", "gated"):
            values = {}
            for name, rows in sources:
                match = next((r for r in rows if r["instance_id"] == iid and r["mode"] == mode
                              and r["note"] == "graded"), None)
                values[name] = match["resolved"] if match else None
            reuse_history.append({"instance_id": iid, "mode": mode, "runs": values,
                                  "varies": len({v for v in values.values() if v is not None}) > 1})
    historical_jobs = [{"instance_id": iid, "mode": mode,
                        "lite_resolved": pairs(lite)[iid][mode]["resolved"],
                        "cached_resolved": pairs(cached)[iid][mode]["resolved"]}
                       for iid in sorted(old_overlap) for mode in ("baseline", "gated")]
    formal_reuse_jobs = [{"instance_id": iid, "mode": mode,
                         "graded": any(r["instance_id"] == iid and r["mode"] == mode and r["note"] == "graded"
                                       for r in formal)}
                        for iid in sorted(expected & set(checks.REUSED_INSTANCES)) for mode in ("baseline", "gated")]
    return {"complete": len(complete) == len(expected) and len(formal) == 2 * len(expected),
            "expected_instances": len(expected), "graded_jobs": sum(r["note"] == "graded" for r in formal),
            "completed_pairs": len(complete), "repositories": len({r["repo"] for r in formal if r["note"] == "graded"}),
            "groups": {name: outcomes(group) for name, group in groups.items()},
            "batches": {"Lite Scale-20": outcomes(pairs(lite)), "Verified 缓存-15": outcomes(pairs(cached))},
            "dedup": {"lite": len(lite_ids), "cached": len(cached_ids), "lite_cached_overlap": len(old_overlap),
                      "formal": len(complete), "formal_new": len(set(complete) - lite_ids - cached_ids),
                      "formal_reused": len(set(complete) & (lite_ids | cached_ids)),
                      "unique_completed_instances": len(lite_ids | cached_ids | set(complete))},
            "costs": costs, "cost_sensitivity": cost_sensitivity, "costs_by_origin": costs_by_origin,
            "elapsed_p90_tradeoffs": elapsed_p90_tradeoffs(cost_sensitivity),
            "cost_origin_counts": {source: sum(origin(r) == source for r in formal if r["note"] == "graded")
                                   for source in sorted({origin(r) for r in formal if r["note"] == "graded"})},
            "elapsed_exclusions": [{"instance_id": r["instance_id"], "mode": r["mode"], "seconds": r["seconds"]}
                                   for r in formal if r["note"] == "graded" and not elapsed_is_comparable(r)],
            "django_gated": [r for r in formal if r["note"] == "graded" and r["mode"] == "gated"
                             and r["instance_id"] in {"django__django-14631", "django__django-16333",
                                                      "django__django-16631", "django__django-16901"}],
            "completion_examples": [r for r in formal if r["note"] == "graded" and r["mode"] == "gated"
                                    and r["instance_id"] in {"astropy__astropy-14369", "matplotlib__matplotlib-23476"}
                                    and r["gate_completed"] == "1"],
            "historical_overlap_jobs": historical_jobs, "formal_reuse_jobs": formal_reuse_jobs,
            "consistency": comparisons, "reuse_history": reuse_history, "anomalies": anomalies}


def render(summary):
    s = summary
    lines = ["## 正式结果" + ("" if s["complete"] else "（中途快照）"), "",
             f"已完成 {s['graded_jobs']} 项官方评分、{s['completed_pairs']}/{s['expected_instances']} 个完整配对，"
             f"评分覆盖 {s['repositories']} 个仓库。仅双组均 graded 的实例进入配对表；缺失不当作 unresolved。", "",
             "| 批次 / 部分 | 配对数 | baseline resolved | gated resolved | baseline-only | gated-only |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, v in {**s["batches"], **s["groups"]}.items():
        lines.append(f"| {name} | {v['pairs']} | {v['baseline']} | {v['gated']} | {v['baseline_only']} | {v['gated_only']} |")
    d = s["dedup"]
    lines += ["", "### 三批实际去重", "", "| 批次 | 完整配对实例数 | 跨批重叠 | 新增不同实例 |",
              "|---|---:|---:|---:|", f"| Lite Scale-20 | {d['lite']} | 0 | {d['lite']} |",
              f"| Verified 缓存-15 | {d['cached']} | {d['lite_cached_overlap']} | {d['cached']-d['lite_cached_overlap']} |",
              f"| Verified 预注册-20 | {d['formal']} | {d['formal_reused']} | {d['formal_new']} |",
              "", f"累计 {d['unique_completed_instances']} 个不同实例；去重计数不保证观测相互独立。", "",
              "### 正式成本", "", "秒数主表排除跨中断耗时；轮数、拦截和补丁字符仍保留该实例。", "",
              "| 部分 | 模式 | 指标 | 口径 | n | 中位数 | P90 |", "|---|---|---|---|---:|---:|---:|"]
    for v in s["costs"]:
        if v["n"]:
            lines.append(f"| {v['group']} | {v['mode']} | {v['metric']} | {v['scope']} | {v['n']} | {v['median']:.1f} | {v['p90']:.1f} |")
    lines += ["", "matplotlib-24970 baseline 的 220,038.7 秒跨两次中断，不计入主要成本 P90；该实例单列，不作为执行效率证据。",
              "原始记录保持不变。以下敏感性表保留包含它的原始墙钟 P90，供核对排除影响。", "",
              "| 部分 | 口径 | baseline n | baseline P90 秒 | gated n | gated P90 秒 |", "|---|---|---:|---:|---:|---:|"]
    for name in s["groups"]:
        for scope in ("全部实例（原始墙钟）", "排除跨中断耗时"):
            values = {v["mode"]: v for v in s["cost_sensitivity"] if v["group"] == name and v["scope"] == scope}
            b, g = values["baseline"], values["gated"]
            if b["n"] or g["n"]:
                def number(v):
                    return "—" if v is None else f"{v:.1f}"
                lines.append(f"| {name} | {scope} | {b['n']} | {number(b['p90'])} | {g['n']} | {number(g['p90'])} |")
    lines += ["", *tradeoff_text(s), "", "### 成本来源分段", "",
              "来源按首次中断归档 CSV 中已评分的 instance+mode 划分。当前评分数：`" + json.dumps(s["cost_origin_counts"], ensure_ascii=False) + "`。",
              "全量时为中断前 9 项、恢复后 31 项评分。补评分沿用原 Agent 的轮数和耗时，"
              "因此“恢复后评分”不等于 Agent 全程使用新规则，也不证明环境稳定；不将其直接称为稳定环境下的成本。",
              "分段成本仍只取双组评分齐全的实例，秒数排除跨中断耗时；每行注明实际 n。", "",
              "| 部分 | 评分来源 | 模式 | 指标 | n | 中位数 | P90 |", "|---|---|---|---|---:|---:|---:|"]
    for v in s["costs_by_origin"]:
        if v["n"]:
            lines.append(f"| {v['group']} | {v['origin']} | {v['mode']} | {v['metric']} | {v['n']} | {v['median']:.1f} | {v['p90']:.1f} |")
    lines += ["",
              "### 重叠实例跨批一致率", "", "| 比较 | 重叠实例 | 已比较 | 一致 | 不一致 |", "|---|---:|---:|---:|---|"]
    for name, v in s["consistency"].items():
        lines.append(f"| {name} | {len(v['overlap'])} | {v['compared']} | {v['consistent']} | {', '.join(v['inconsistent']) or '—'} |")
    lines += ["", "一致指同一实例 baseline/gated 两个二元结果均一致。前两批相互重叠的 4 个实例与本批的交集按实际 ID 计算。",
              f"逐项核对：前两批的 {len(s['historical_overlap_jobs'])} 个重叠 instance+mode 比较均有记录；"
              f"本批跨批复用 {len(s['formal_reuse_jobs'])} 项任务，已 graded "
              f"{sum(v['graded'] for v in s['formal_reuse_jobs'])} 项。两类键清单分别保存在汇总 JSON 的 "
              "`historical_overlap_jobs` 和 `formal_reuse_jobs`，不能把前者的 8 项当作本批有 8 项复用任务。", "",
              "### 两个复用实例的三次结果", "", "| 实例 | 模式 | 缓存批 pilot | 缓存-15 | 正式批 | 运行间变异 |",
              "|---|---|---|---|---|---|"]
    for v in s["reuse_history"]:
        vals = ["待完成" if x is None else ("resolved" if x == "1" else "unresolved") for x in v["runs"].values()]
        lines.append("| " + " | ".join([v["instance_id"], v["mode"], *vals, "有" if v["varies"] else "未观察到"]) + " |")
    lines += ["", "此表缓存批 pilot 使用 `.pytest_tmp/verified15/pilot5_results.csv`，与链路 pilot-5 是不同运行。"
              "Flask 另有单实例 pilot 的 54 轮记录，未混入此表的缓存批 pilot-5（15 轮）。"
              "结果差异记录为运行间变异；代码与环境存在变化，不直接归因于门禁或模型采样。", "",
              "### 异常实例", "", "| 实例 | 模式 | 标记 |", "|---|---|---|"]
    for v in s["anomalies"]:
        lines.append("| " + " | ".join([v["instance_id"], v["mode"], ", ".join(v["tags"])]) + " |")
    reverse = [v for v in s["anomalies"]
               if "gate_complete_official_unresolved_requires_A_B_C_review" in v["tags"]]
    lines += ["", f"### 门禁完成但官方未通过：正式批 {len(reverse)} 项", "",
              "此处只计正式批 gated，不把 Astropy pilot 的 A 类混入正式批计数；Astropy 正式批双组 resolved。",
              "| 正式实例 | 归类 | 待核验依据 |", "|---|---|---|"]
    for v in reverse:
        lines.append(f"| {v['instance_id']} | 机制判断见统一审计记录 | 已审与新增项分开核对；自动标签不代替环境核验、同补丁重放或测试完整性审查 |")
    if not reverse:
        lines.append("| 无 | — | 继续监测新增组合 |")
    lines += ["", "A 为测试覆盖不足；B 为测试篡改；C 为 delivery_clean 缺陷。仅凭两个结果标记不能判定归类，"
              "也不能断言门禁通过是官方 resolved 的必要条件；官方 resolved 且门禁未完成的观察另列。",
              "[统一审计记录](verified20-formal-gate-audit.md) 保留 Requests 环境混杂与 Xarray 的 A/B 两项证据；新增项仍须核验。",
              "[Seaborn 专项对照](verified20-seaborn-comparison.md) 已核对缓存批 pilot 与正式批的补丁及阶段；共同触达 60 轮不能单独解释官方结果差异。"]
    if len(s["django_gated"]) == 4:
        rows = s["django_gated"]
        lines += ["", "Django gated 的 4 项运行依次为 " + "；".join(
            f"{r['instance_id']}：{r['turns']} 轮、阶段 {r['gate_final_stage']}、gate_completed={r['gate_completed']}"
            for r in rows) + "。官方结果为 " + str(sum(r['resolved'] == '1' for r in rows)) + " resolved、" +
            str(sum(r['resolved'] == '0' for r in rows)) + " unresolved。",
            "这些运行普遍触达或接近 60 轮上限，停在阶段 4/5。门禁未完成不能直接解读为官方评分受阻；"
            "3 个实例官方 resolved，可作为门禁未必阻断官方评分的观察，不作为门禁全流程有效的证据。"
            "这一模式与轮数预算和多阶段开销的交互相容；没有增加轮数预算的对照实验，尚不能确定因果。"]
    for r in s["completion_examples"]:
        lines += ["", f"{r['instance_id']} gated：{r['turns']} 轮、{r['gate_intercepts']} 次拦截、"
                  f"阶段 {r['gate_final_stage']}、gate_completed=1、{int(r['diff_chars']):,} 字符。"]
    if any(r["instance_id"] == "matplotlib__matplotlib-23476" for r in s["completion_examples"]):
        lines += ["", "正式批最早完成全流程的是 Astropy-14369；Matplotlib-23476 是另一个接近 60 轮上限仍完成的实例。"
                  "它表明该预算下存在完成全流程的案例，60 轮不是绝对阻断，但不能据此确定 Django 未完成的原因。"]
    lines += ["", "门禁完成且官方 unresolved 的实例仍须逐项审计 A/B/C；零拦截须审计 benign/suspicious。自动统计不替代这些机制审查。", "",
              "## 局限与结论", "", "- 全量三批涉及 49 个不同实例（名义 n=49），不保证观测独立，不足以宣称 p<0.05。",
              "- 本批 2 个复用实例非完全独立。",
              "- 缓存-15 存在选择偏差。",
              "- 容器无 chroot，Agent 可读门禁源码。",
              "- 门禁通过不等于官方通过，保留 Astropy A 类历史案例。",
              "- 三批不合并 pooled 成功率或显著性检验；pilot 及审计复核不进入正式统计。",
              "- 运行间代码、环境与中断会影响结果及耗时，不能将观察到的差异单独归因于门禁。",
              "- Xarray 重放证实阶段 2 的关键自写断言在阶段 3 被替换，最终测试通过而原登记断言失败；"
              "当前门禁未保证测试演进保留既定行为覆盖。证据与 A/B 两项解释见 [统一审计](verified20-formal-gate-audit.md)，不单独归因于官方 warning 失败。",
              "- 成本 P90 样本量有限且对离群值敏感；2.16×→1.87× 是同一正式批随样本增加的中途漂移。"
              "最终仅在全部 40 项证据齐全后重算，独立配对 n=18，排除跨中断耗时后的时间 n 为 baseline 17、gated 18；比值视为点估计。",
              "- 扩展到 100+ 实例仅为规划，不能保证显著性；所需样本量取决于效应及配对不一致率。", ""]
    if not s["complete"]:
        lines.append("正式批尚未全部完成，暂不对三批方向一致性下结论。")
    elif any(v["baseline_only"] for v in {**s["batches"], **s["groups"]}.values()):
        changed = [name for name, v in {**s["batches"], **s["groups"]}.items() if v["baseline_only"]]
        lines.append("出现 baseline-only 的批次/部分：" + "、".join(changed) + "。需结合逐实例结果如实分析，不能宣称三批均未出现退步。")
    else:
        lines.append("三批均未观察到 baseline-only；这不足以证明显著提升，正式结果与上述局限并列解释。")
    return "\n".join(lines) + "\n"


def cost_review(summary):
    lines = ["### 跨中断耗时：成本口径修正（自动更新）", "",
             "截至 " + summary.get("checked_at", "本次快照") + "；仅计双组均 graded 的配对，独立与复用分开。", "",
             "matplotlib-24970 baseline 的 220,038.7 秒跨两次中断，不计入主要成本 P90；该实例单列，不作为执行效率证据。",
             "原始秒数和其他指标保留。文末运行快照的 seconds 表是原始墙钟核对口径，主要效率口径见下表的排除行。", "",
             "| 部分 | 口径 | baseline n | baseline P90 秒 | gated n | gated P90 秒 |", "|---|---|---:|---:|---:|---:|"]
    for group in summary["groups"]:
        for scope in ("全部实例（原始墙钟）", "排除跨中断耗时"):
            data = {v["mode"]: v for v in summary["cost_sensitivity"] if v["group"] == group and v["scope"] == scope}
            b, g = data["baseline"], data["gated"]
            if not b["n"] and not g["n"]:
                continue
            bv = "—" if b["p90"] is None else f"{b['p90']:.1f}"
            gv = "—" if g["p90"] is None else f"{g['p90']:.1f}"
            lines.append(f"| {group} | {scope} | {b['n']} | {bv} | {g['n']} | {gv} |")
    lines += ["", *tradeoff_text(summary), "", "评分来源：`" + json.dumps(summary["cost_origin_counts"], ensure_ascii=False) + "`。",
              "来源按首次中断的归档评分键划分，完整分段成本见 `formal20_logicstar/report_preview.md`，最终表由同一程序生成。"
              "恢复后补评分沿用原 Agent 统计，不能将 9/31 的评分分段直接解释为新旧 Agent 运行分段或稳定环境对照。", ""]
    return "\n".join(lines)


def update_cost_review(path, summary):
    path = Path(path)
    original = path.read_text(encoding="utf-8")
    begin, end = "<!-- FORMAL_COST_REVIEW -->", "<!-- END_FORMAL_COST_REVIEW -->"
    block = begin + "\n\n" + cost_review(summary) + "\n" + end + "\n\n"
    text = original
    if begin in text:
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        text = text[:start] + block.rstrip() + text[stop:]
    else:
        at = text.index("## 正式结果")
        text = text[:at] + block + text[at:]
    text = text.replace("### 正式批成本与异常", "### 正式批原始墙钟成本与异常（仅核对）")
    # A supervisor may have just refreshed its snapshot; retry on the next poll.
    if path.read_text(encoding="utf-8") != original:
        return False
    tmp = path.with_name(path.name + ".cost.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    work = ROOT / ".pytest_tmp/verified20"
    parser.add_argument("--results", type=Path, default=work / "formal20_logicstar/results.csv")
    parser.add_argument("--tasks", type=Path, default=work / "verified20_logicstar.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--update-main", action="store_true")
    parser.add_argument("--update-costs", action="store_true")
    parser.add_argument("--initial-graded-results", type=Path)
    args = parser.parse_args()
    if args.update_main and args.update_costs:
        raise ValueError("Use either finalization or the live cost update")
    plan = checks.build_resume_plan(args.results.resolve(), args.tasks.resolve())
    if not args.allow_partial and plan["counts"]["skip"] != plan["total_jobs"]:
        raise ValueError("Formal batch is incomplete; missing results are not unresolved")
    if args.update_main:
        proof_path = args.results.parent / "completion_verification.json"
        if not proof_path.exists():
            raise ValueError("Missing completion_verification.json; refusing to finalize")
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if not proof.get("passed") or proof.get("total_jobs") != plan["total_jobs"] or (
                proof.get("counts", {}).get("skip") != plan["total_jobs"]):
            raise ValueError("Incomplete completion evidence; refusing to finalize")
    paths = [args.results, ROOT / "benchmarks/swebench/results/scale20_lite_container.csv",
             ROOT / "benchmarks/swebench/results/verified15_cached.csv", work / "pilot5_logicstar/results.csv",
             ROOT / ".pytest_tmp/verified15/pilot5_results.csv"]
    initial = args.initial_graded_results
    if initial is None:
        archives = sorted(args.results.parent.glob("recovery_*/results.csv"))
        if not archives:
            raise ValueError("Missing pre-interruption results for cost provenance")
        initial = archives[0]
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if isinstance(tasks, dict):
        tasks = next(tasks[k] for k in ("tasks", "instances", "rows", "data") if isinstance(tasks.get(k), list))
    summary = summarize(*(read_rows(p) for p in paths), tasks, initial_graded=read_rows(initial))
    summary.update(checked_at=datetime.now(timezone.utc).isoformat(),
                   generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   sources={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*paths, initial]})
    text = render(summary)
    args.output.write_text(text, encoding="utf-8")
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.update_costs:
        update_cost_review(ROOT / "docs/benchmarks/verified20-preregistered-report.md", summary)
    if args.update_main:
        if not summary["complete"]:
            raise ValueError("Refusing to finalize an incomplete report")
        report = ROOT / "docs/benchmarks/verified20-preregistered-report.md"
        old = report.read_text(encoding="utf-8")
        begin, stop = "<!-- FORMAL_COST_REVIEW -->", "<!-- END_FORMAL_COST_REVIEW -->"
        if begin in old:
            at = old.index(begin)
            end = old.index(stop, at) + len(stop)
            old = old[:at] + old[end:]
        marker = "<!-- FORMAL_RUNTIME_SNAPSHOT -->"
        start = old.index("## 正式结果")
        end = old.index(marker, start)
        old = old[:start] + text + "\n" + old[end:]
        old = old.replace("### 正式批成本与异常", "### 正式批原始墙钟成本与异常（仅核对）")
        old = old.replace("# Verified 预注册-20：执行报告（正式批运行中）", "# Verified 预注册-20：执行报告（正式评分完成）")
        old = old.replace("- 预注册 20 实例、12 仓库；正式批实际执行 N 个、覆盖 K 个仓库：待填。",
                          f"- 预注册 20 实例、12 仓库；正式批 {summary['completed_pairs']} 实例双组评分完成，覆盖 {summary['repositories']} 个仓库。")
        for iid in checks.REUSED_INSTANCES:
            row = next(r for r in read_rows(args.results) if r["instance_id"] == iid and r["mode"] == "gated")
            cached = next(r for r in read_rows(paths[2]) if r["instance_id"] == iid and r["mode"] == "gated")
            value = "resolved" if row["resolved"] == "1" else "unresolved"
            before = f"| {iid} | resolved，{cached['turns']} 轮 | 待填 | 待填 |"
            after = f"| {iid} | resolved，{cached['turns']} 轮 | {value}，{row['turns']} 轮 | {'是' if row['resolved']==cached['resolved'] else '否'} |"
            old = old.replace(before, after)
        report.write_text(old, encoding="utf-8", newline="\n")
    print(json.dumps({"complete": summary["complete"], "graded_jobs": summary["graded_jobs"],
                      "pairs": summary["completed_pairs"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
