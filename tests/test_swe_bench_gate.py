"""SWE-bench 门禁基准脚本冒烟测试（v0.30.0）。

小规模运行 benchmarks/swe_bench_gate.py，验证：
- 聚合指标结构完整、数值在合理区间；
- 全 SOP / 全跳步+放弃 的极端参数下行为符合预期；
- 阈值检查函数判定正确；
- CLI 入口可输出纯 JSON 且退出码正确。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_bench():
    spec = importlib.util.spec_from_file_location(
        "pb_swe_bench_smoke", ROOT / "benchmarks" / "swe_bench_gate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_swe_bench_smoke"] = module
    spec.loader.exec_module(module)
    return module


def test_run_swe_bench_gate_smoke():
    mod = _load_bench()
    results = mod.run_swe_bench_gate(tasks=5, seed=1)
    assert results["benchmark"] == "swe-bench-gate"
    assert results["n_tasks"] == 5
    assert 0.0 <= results["sop_compliance_rate"] <= 1.0
    assert results["sop_compliance_rate"] >= 0.6
    assert results["shortcut_interception_rate"] == 1.0
    assert results["avg_interceptions_per_task"] <= 2.0
    assert sum(results["kind_counts"].values()) == 5
    assert sum(results["final_stage_distribution"].values()) == 5
    assert len(results["tasks"]) == 5


def test_run_swe_bench_gate_all_sop():
    mod = _load_bench()
    results = mod.run_swe_bench_gate(tasks=5, seed=3, sop_rate=1.0, fake_test_rate=0.0)
    assert results["sop_compliance_rate"] == 1.0
    assert results["shortcut_interception_rate"] == 1.0
    assert all(task["final_stage"] == 6 for task in results["tasks"])


def test_run_swe_bench_gate_all_shortcut_give_up():
    mod = _load_bench()
    results = mod.run_swe_bench_gate(tasks=5, seed=5, sop_rate=0.0, give_up_rate=1.0)
    assert results["kind_counts"]["shortcut"] == 5
    assert results["shortcut_interception_rate"] == 1.0
    assert results["avg_interceptions_per_task"] == 2.0
    assert results["sop_compliance_rate"] == 0.0
    assert all(task["gave_up"] for task in results["tasks"])


def test_check_thresholds_overflow():
    mod = _load_bench()
    results = {
        "sop_compliance_rate": 0.5,
        "shortcut_interception_rate": 0.8,
        "avg_interceptions_per_task": 6.0,
    }
    failures = mod.check_thresholds(
        results,
        min_compliance=0.8,
        min_interception=0.9,
        max_interceptions_per_task=5.0,
    )
    assert len(failures) == 3


def test_check_thresholds_all_pass():
    mod = _load_bench()
    results = {
        "sop_compliance_rate": 0.95,
        "shortcut_interception_rate": 1.0,
        "avg_interceptions_per_task": 1.5,
    }
    assert mod.check_thresholds(results) == []


def test_main_json_output(capsys):
    mod = _load_bench()
    rc = mod.main(["--json", "--tasks", "3", "--seed", "7"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_tasks"] == 3
    assert data["benchmark"] == "swe-bench-gate"
