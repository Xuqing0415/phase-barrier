"""Report denominators must follow complete pairs and actual ID intersections."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verified_report", Path(__file__).resolve().parents[1] / "scripts/verified20_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def row(iid, mode, resolved="1", **extra):
    return {"instance_id": iid, "repo": iid.split("__")[0], "mode": mode, "resolved": resolved,
            "note": "graded", "turns": "20", "seconds": "100", "gate_intercepts": "1",
            "diff_chars": "1000", "gate_completed": "0", "gate_final_stage": "4", **extra}


def pair(iid, baseline="1", gated="1"):
    return [row(iid, "baseline", baseline), row(iid, "gated", gated)]


def test_missing_or_infrastructure_row_does_not_enter_pair_denominator():
    rows = pair("x__1") + [row("x__2", "baseline"), row("x__2", "gated", "", note="model_call_error")]
    assert report.outcomes(report.pairs(rows)) == {
        "pairs": 1, "baseline": 1, "gated": 1, "baseline_only": 0, "gated_only": 0}


def test_consistency_does_not_assume_old_overlap_is_in_formal():
    old = pair("x__old") + pair("x__flip", "0", "0") + pair("x__pending")
    now = pair("x__flip") + [row("x__pending", "baseline")]
    result = report.consistency(old, now, {"x__flip", "x__pending", "x__new"})
    assert result["overlap"] == ["x__flip", "x__pending"]
    assert result["compared"] == 1 and result["consistent"] == 0
    assert result["inconsistent"] == ["x__flip"] and result["pending"] == ["x__pending"]


def test_separate_reuse_costs_and_count_same_double_failure_without_discordance():
    reused = "mwaskom__seaborn-3069"
    formal = pair("x__new", "0", "0") + pair(reused)
    formal[-1]["seconds"] = "10000"
    summary = report.summarize(formal, pair("x__old"), pair(reused), [], [],
                               [{"instance_id": iid} for iid in ("x__new", reused)])
    assert summary["complete"] and summary["groups"]["独立部分"]["pairs"] == 1
    assert summary["groups"]["独立部分"]["baseline_only"] == 0
    costs = {(r["group"], r["mode"], r["metric"]): r for r in summary["costs"]}
    assert costs["独立部分", "gated", "seconds"]["median"] == 100
    assert costs["复用部分", "gated", "seconds"]["median"] == 10000
    assert summary["dedup"]["unique_completed_instances"] == 3


def test_p90_interpolates_and_empty_data_has_no_fake_zero():
    assert report.percentile([10, 0], .9) == 9
    assert report.percentile([], .9) is None


def test_regression_in_reused_pair_is_not_hidden_in_conclusion():
    iid = "pallets__flask-5014"
    summary = report.summarize(pair(iid, "1", "0"), [], [], [], [], [{"instance_id": iid}])
    rendered = report.render(summary)
    assert "出现 baseline-only 的批次/部分：复用部分" in rendered
    assert "三批均未观察到 baseline-only" not in rendered


def test_duplicate_source_rows_are_rejected(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("instance_id,mode,resolved,note\nx,baseline,1,graded\nx,baseline,0,graded\n")
    with pytest.raises(ValueError, match="Duplicate"):
        report.read_rows(path)


def test_finalize_refuses_incomplete_official_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(report.checks, "build_resume_plan", lambda *a: {"counts": {"skip": 39}, "total_jobs": 40})
    out, summary = tmp_path / "report.md", tmp_path / "summary.json"
    monkeypatch.setattr(report, "__name__", "imported")
    monkeypatch.setattr("sys.argv", ["report", "--output", str(out), "--summary", str(summary), "--update-main"])
    with pytest.raises(ValueError, match="incomplete"):
        report.main()
    assert not out.exists() and not summary.exists()


def test_elapsed_exclusion_preserves_result_other_metrics_and_gated_p90():
    iid = "matplotlib__matplotlib-24970"
    formal = pair(iid) + pair("x__ordinary")
    formal[0]["seconds"] = "220038.7"
    formal[0]["turns"] = "37"
    formal[2]["seconds"] = "200"
    summary = report.summarize(formal, [], [], [], [], [{"instance_id": i} for i in (iid, "x__ordinary")])
    assert summary["groups"]["独立部分"]["baseline"] == 2
    costs = {(v["mode"], v["metric"]): v for v in summary["costs"] if v["group"] == "独立部分"}
    assert costs["baseline", "seconds"]["n"] == 1 and costs["baseline", "seconds"]["p90"] == 200
    assert costs["baseline", "turns"]["n"] == 2
    sensitivity = {(v["mode"], v["scope"]): v for v in summary["cost_sensitivity"] if v["group"] == "独立部分"}
    assert sensitivity["baseline", "全部实例（原始墙钟）"]["p90"] > 100000
    assert sensitivity["gated", "全部实例（原始墙钟）"]["p90"] == sensitivity["gated", "排除跨中断耗时"]["p90"]
    assert formal[0]["seconds"] == "220038.7"


def test_cost_origin_uses_archived_job_keys_not_csv_order():
    formal = pair("x__1") + pair("x__2")
    original = [formal[3], formal[0]]
    summary = report.summarize(list(reversed(formal)), [], [], [], [],
                               [{"instance_id": "x__1"}, {"instance_id": "x__2"}], initial_graded=original)
    assert summary["cost_origin_counts"] == {"中断前已评分": 2, "恢复后评分（含补评）": 2}
    assert sum(v["n"] for v in summary["costs_by_origin"] if v["metric"] == "seconds") == 4
    assert "不等于 Agent 全程使用新规则" in report.render(summary)


def test_host_sleep_excludes_both_sympy_times_without_discarding_outcomes():
    iid = "sympy__sympy-18211"
    formal = pair(iid) + pair("x__ordinary")
    formal[0]["seconds"], formal[1]["seconds"] = "9070.8", "8814.0"
    summary = report.summarize(formal, [], [], [], [], [{"instance_id": i} for i in (iid, "x__ordinary")])
    costs = {(v["mode"], v["metric"]): v for v in summary["costs"] if v["group"] == "独立部分"}
    for mode in ("baseline", "gated"):
        assert costs[mode, "seconds"]["n"] == 1
        assert costs[mode, "seconds"]["p90"] == 100
        assert costs[mode, "turns"]["n"] == 2
    assert summary["groups"]["独立部分"]["baseline"] == 2
    assert len(summary["elapsed_exclusions"]) == 2
    assert "宿主机睡眠" in report.render(summary)


def test_completion_examples_do_not_call_matplotlib_first():
    formal = pair("astropy__astropy-14369") + pair("matplotlib__matplotlib-23476")
    for r in formal:
        if r["mode"] == "gated":
            r.update(gate_completed="1", gate_final_stage="6")
    summary = report.summarize(formal, [], [], [], [], [{"instance_id": r["instance_id"]} for r in formal[::2]])
    assert "正式批最早完成全流程的是 Astropy-14369" in report.render(summary)


def test_full_rows_without_completion_proof_cannot_finalize(monkeypatch, tmp_path):
    monkeypatch.setattr(report.checks, "build_resume_plan", lambda *a: {"counts": {"skip": 40}, "total_jobs": 40})
    monkeypatch.setattr("sys.argv", ["report", "--results", str(tmp_path / "results.csv"),
                                   "--output", str(tmp_path / "report.md"),
                                   "--summary", str(tmp_path / "summary.json"), "--update-main"])
    with pytest.raises(ValueError, match="Missing completion_verification"):
        report.main()


def test_live_cost_update_preserves_manual_text_and_replaces_its_own_block(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# 手工报告\n\n已核验的异常说明。\n\n## 正式结果（待填）\n\n待填\n\n"
                    "<!-- FORMAL_RUNTIME_SNAPSHOT -->\n\n### 正式批成本与异常\n", encoding="utf-8")
    summary = report.summarize(pair("x__1"), [], [], [], [], [{"instance_id": "x__1"}])
    summary["checked_at"] = "2026-11-13T02:00:00+00:00"
    assert report.update_cost_review(path, summary)
    summary["checked_at"] = "2026-11-13T02:15:00+00:00"
    assert report.update_cost_review(path, summary)
    text = path.read_text(encoding="utf-8")
    assert text.count("<!-- FORMAL_COST_REVIEW -->") == 1
    assert "已核验的异常说明。" in text and "## 正式结果（待填）" in text
    assert "02:15:00" in text and "02:00:00" not in text
    assert "原始墙钟成本与异常（仅核对）" in text


def test_cost_tradeoff_is_recomputed_and_keeps_denominators():
    sensitivity = [dict(group="独立部分", mode="baseline", scope="排除跨中断耗时", n=6, p90=538.95),
                   dict(group="独立部分", mode="gated", scope="排除跨中断耗时", n=7, p90=1163.74)]
    first = report.elapsed_p90_tradeoffs(sensitivity)[0]
    assert first["ratio"] == pytest.approx(2.159273, abs=.00001)
    assert first["baseline_n"] == 6 and first["gated_n"] == 7
    sensitivity[0].update(n=7, p90=587.02)
    sensitivity[1].update(n=8, p90=1098.68)
    second = report.elapsed_p90_tradeoffs(sensitivity)[0]
    assert second["ratio"] == pytest.approx(1.87162277)
    assert "1.87×" in report.tradeoff_text({"elapsed_p90_tradeoffs": [second]})[0]


@pytest.mark.parametrize("baseline_p90", [0, None])
def test_cost_tradeoff_does_not_invent_ratio_for_missing_or_zero_cost(baseline_p90):
    rows = [dict(group="独立部分", mode="baseline", scope="排除跨中断耗时", n=0, p90=baseline_p90),
            dict(group="独立部分", mode="gated", scope="排除跨中断耗时", n=1, p90=100)]
    assert report.elapsed_p90_tradeoffs(rows)[0]["ratio"] is None
