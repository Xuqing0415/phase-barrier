"""性能基准 harness 冒烟测试（v0.27.0）。

小规模运行基准函数，验证：
- 结果结构完整、延迟统计为正数；
- 多线程并发无异常（errors == 0）；
- 阈值检查函数在越界 / 达标两种情况下判定正确。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_bench():
    spec = importlib.util.spec_from_file_location(
        "pb_bench_smoke", ROOT / "benchmarks" / "bench.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_bench_smoke"] = module
    spec.loader.exec_module(module)
    return module


def test_bench_state_contention_smoke():
    mod = _load_bench()
    result = mod.bench_state_contention(threads=8, iters=3)
    assert result["count"] == 24
    assert result["errors"] == 0
    assert result["p50_ms"] >= 0
    assert result["p95_ms"] >= result["p50_ms"] or result["count"] == 1
    assert result["throughput_ops_per_sec"] > 0


def test_bench_sidecar_write_smoke():
    mod = _load_bench()
    result = mod.bench_sidecar_write(write_ops=8, workers=4)
    assert result["count"] >= 8
    assert result["p50_ms"] >= 0
    assert result["p95_ms"] >= 0


def test_bench_sidecar_exec_smoke():
    mod = _load_bench()
    result = mod.bench_sidecar_exec(exec_ops=4, workers=2)
    assert result["count"] >= 4
    assert result["p50_ms"] >= 0
    assert result["p95_ms"] >= 0


def test_bench_check_thresholds_overflow():
    mod = _load_bench()
    results = {
        "state": {"p95_ms": 1500.0},
        "sidecar_write": {"p95_ms": 100.0},
        "sidecar_exec": {"p95_ms": 6000.0},
    }
    failures = mod.check_thresholds(
        results,
        state_p95_ms=1000.0,
        write_p95_ms=2000.0,
        exec_p95_ms=5000.0,
    )
    assert len(failures) == 2
    assert any("state" in item for item in failures)
    assert any("sidecar_exec" in item for item in failures)


def test_bench_check_thresholds_p99_overflow():
    mod = _load_bench()
    results = {
        "state": {"p95_ms": 100.0, "p99_ms": 5000.0},
        "sidecar_write": {"p95_ms": 100.0, "p99_ms": 9000.0},
        "sidecar_exec": {"p95_ms": 100.0, "p99_ms": 20000.0},
    }
    failures = mod.check_thresholds(
        results,
        state_p99_ms=1000.0,
        write_p99_ms=2000.0,
        exec_p99_ms=3000.0,
    )
    assert len(failures) == 3
    assert any("p99" in item and "state" in item for item in failures)
    assert any("p99" in item and "sidecar_write" in item for item in failures)
    assert any("p99" in item and "sidecar_exec" in item for item in failures)


def test_bench_check_thresholds_all_pass():
    mod = _load_bench()
    results = {
        "state": {"p95_ms": 100.0},
        "sidecar_write": {"p95_ms": 200.0},
        "sidecar_exec": {"p95_ms": 300.0},
    }
    assert mod.check_thresholds(results) == []