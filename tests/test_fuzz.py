"""解析器模糊测试基准冒烟测试（v0.31.0）。

小规模运行 benchmarks/fuzz_parsers.py，验证：
- 聚合指标结构完整、崩溃率为 0；
- 阈值检查函数判定正确；
- CLI 入口可输出纯 JSON 且退出码正确。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_fuzz():
    spec = importlib.util.spec_from_file_location(
        "pb_fuzz_smoke", ROOT / "benchmarks" / "fuzz_parsers.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_fuzz_smoke"] = module
    spec.loader.exec_module(module)
    return module


def test_run_fuzz_smoke():
    mod = _load_fuzz()
    results = mod.run_fuzz(iterations=120, seed=1)
    assert results["benchmark"] == "parser-fuzz"
    assert results["n_targets"] == 10
    assert results["n_cases"] == 10 * 120
    assert results["crashes"] == 0
    assert results["crash_rate"] == 0.0
    assert all(v == 0 for v in results["target_crashes"].values())


def test_run_fuzz_other_seed():
    mod = _load_fuzz()
    results = mod.run_fuzz(iterations=80, seed=20260902)
    assert results["crashes"] == 0
    assert results["n_targets"] == 10


def test_check_thresholds_overflow():
    mod = _load_fuzz()
    results = {
        "crash_rate": 0.05,
        "target_crashes": {"summarize_test_output": 3, "extract_coverage": 0},
    }
    failures = mod.check_thresholds(results, max_crash_rate=0.0)
    assert len(failures) == 2
    assert any("summarize_test_output" in item for item in failures)
    assert any("崩溃率" in item for item in failures)


def test_check_thresholds_all_pass():
    mod = _load_fuzz()
    results = {
        "crash_rate": 0.0,
        "target_crashes": dict.fromkeys(("a", "b"), 0),
    }
    assert mod.check_thresholds(results) == []


def test_main_json_output(capsys):
    mod = _load_fuzz()
    rc = mod.main(["--json", "--iterations", "30", "--seed", "3"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["benchmark"] == "parser-fuzz"
    assert data["n_cases"] == 10 * 30
    assert data["crashes"] == 0


def test_main_fail_fast_accepts_clean():
    mod = _load_fuzz()
    rc = mod.main(["--fail-fast", "--iterations", "20", "--seed", "1"])
    assert rc == 0


def test_main_fail_fast_rejects_crash(monkeypatch):
    mod = _load_fuzz()
    bad = mod.run_fuzz(iterations=10, seed=1)
    bad["crash_rate"] = 0.1
    bad["target_crashes"]["summarize_test_output"] = 10
    monkeypatch.setattr(mod, "run_fuzz", lambda iterations=1000, seed=42: bad)
    rc = mod.main(["--fail-fast", "--iterations", "20", "--seed", "1"])
    assert rc == 1
