"""benchmarks/swebench/analyze_results.py 的回归测试（2026-09）。

报告里的每个数字都靠这个脚本复算，所以口径（Wilson 区间、被门禁救回/拖累的判定、
空补丁计数）必须有测试钉住，避免报告与 CSV 悄悄漂移。
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "swebench" / "analyze_results.py"
COLUMNS = [
    "instance_id",
    "repo",
    "mode",
    "resolved",
    "turns",
    "seconds",
    "diff_chars",
    "gate_intercepts",
    "gate_final_stage",
    "gate_completed",
    "note",
]


def _load():
    spec = importlib.util.spec_from_file_location("analyze_results", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze = _load()


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{column: "" for column in COLUMNS}, **row})
    return path


def _row(instance_id: str, mode: str, resolved: str, **overrides: str) -> dict[str, str]:
    base = {
        "instance_id": instance_id,
        "repo": instance_id.split("__")[0],
        "mode": mode,
        "resolved": resolved,
        "turns": "40",
        "seconds": "300.0",
        "diff_chars": "2000",
        "gate_intercepts": "0" if mode == "baseline" else "3",
        "gate_final_stage": "" if mode == "baseline" else "6",
        "gate_completed": "" if mode == "baseline" else "1",
        "note": "graded",
    }
    base.update(overrides)
    return base


def test_wilson_interval_matches_known_values():
    low, high = analyze.wilson(18, 20)
    assert round(low, 3) == 0.699
    assert round(high, 3) == 0.972
    low, high = analyze.wilson(20, 20)
    assert round(low, 3) == 0.839
    assert round(high, 6) == 1.0
    assert analyze.wilson(0, 0) == (0.0, 0.0)


def test_summarize_counts_rates_and_gate_columns(tmp_path):
    path = _write(
        tmp_path / "results.csv",
        [
            _row("django__django-1", "baseline", "1"),
            _row("django__django-1", "gated", "1", gate_intercepts="5"),
            _row("sympy__sympy-2", "baseline", "0", diff_chars="0"),
            _row("sympy__sympy-2", "gated", "1", gate_intercepts="2"),
            _row("sympy__sympy-3", "baseline", "1"),
            _row("sympy__sympy-3", "gated", "0", gate_final_stage="4", gate_completed="0"),
        ],
    )
    summary = analyze.summarize(analyze._load(path), path)
    base = summary["modes"]["baseline"]
    gated = summary["modes"]["gated"]
    assert (base["n"], base["resolved"]) == (3, 2)
    assert (gated["n"], gated["resolved"]) == (3, 2)
    assert base["empty_patch"] == 1
    assert gated["intercepts"] == 10
    assert gated["stage6"] == 2
    assert gated["delivery_clean"] == 2
    # sympy-2 被救回（baseline 失败 → gated 成功）；sympy-3 被拖累
    assert summary["rescued_by_gate"] == ["sympy__sympy-2"]
    assert summary["regressed_under_gate"] == ["sympy__sympy-3"]


def test_markdown_render_reports_intervals_and_attribution(tmp_path):
    path = _write(
        tmp_path / "results.csv",
        [
            _row("django__django-1", "baseline", "0"),
            _row("django__django-1", "gated", "1", gate_intercepts="4"),
        ],
    )
    summary = analyze.summarize(analyze._load(path), path)
    text = analyze.render_markdown(summary)
    assert "| baseline | 1 | 0 | 0% |" in text
    assert "| gated | 1 | 1 | 100% |" in text
    assert "`django__django-1`" in text
    assert "被门禁救回（baseline 失败 → gated 成功，1 个）" in text


def test_real_scale20_csv_still_matches_published_numbers():
    """仓库里入库的 CSV 必须仍然算出报告里的 18/20 与 20/20（防漂移）。"""
    path = ROOT / "benchmarks" / "swebench" / "results" / "scale20_lite_container.csv"
    if not path.exists():  # pragma: no cover - 数据文件随仓库分发，缺失即失败
        raise AssertionError(f"missing {path}")
    summary = analyze.summarize(analyze._load(path), path)
    assert summary["modes"]["baseline"]["resolved"] == 18
    assert summary["modes"]["baseline"]["n"] == 20
    assert summary["modes"]["gated"]["resolved"] == 20
    assert summary["modes"]["gated"]["intercepts"] == 78
    assert summary["modes"]["gated"]["delivery_clean"] == 14
    assert summary["modes"]["gated"]["empty_patch"] == 0
    assert summary["rescued_by_gate"] == [
        "matplotlib__matplotlib-22711",
        "sympy__sympy-11400",
    ]
    assert summary["regressed_under_gate"] == []
