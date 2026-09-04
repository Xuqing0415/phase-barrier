"""sidecar HTTP 与并发锁模糊基准冒烟测试（v0.35.0）。

小规模运行 benchmarks/fuzz_sidecar.py，验证：
- sidecar HTTP 边界模糊：真实服务器随机请求下 0 崩溃（连接 / 500 / 不可解析响应）；
- 多进程并发锁压力：计数一致、无临时文件残留（POSIX；Windows 上跳过子进程用例）；
- 聚合指标结构、阈值检查、CLI JSON 输出与 --fail-fast 拒绝路径。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_fuzz():
    spec = importlib.util.spec_from_file_location(
        "pb_fuzz_sidecar_smoke", ROOT / "benchmarks" / "fuzz_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_fuzz_sidecar_smoke"] = module
    spec.loader.exec_module(module)
    return module


_MP_OK = sys.platform != "win32"
skip_windows_mp = pytest.mark.skipif(
    not _MP_OK,
    reason="multiprocessing spawn 无法从测试加载的合成模块名导入 worker（Windows 本地跳过，CLI 场景可用）",
)


def test_run_fuzz_http_only_smoke():
    mod = _load_fuzz()
    results = mod.run_fuzz(http_iterations=20, lock_rounds=0, seed=1)
    assert results["benchmark"] == "sidecar-fuzz"
    assert results["n_targets"] == 2
    assert results["n_cases"] == 20
    assert results["crashes"] == 0
    assert results["target_crashes"]["sidecar_http"] == 0


def test_run_fuzz_deterministic_same_seed():
    mod = _load_fuzz()
    a = mod.run_fuzz(http_iterations=15, lock_rounds=0, seed=7)
    b = mod.run_fuzz(http_iterations=15, lock_rounds=0, seed=7)
    assert a["crashes"] == b["crashes"]
    assert a["target_crashes"] == b["target_crashes"]
    assert a["config"] == b["config"]


@skip_windows_mp
def test_lock_round_integrity():
    """一轮并发锁：持锁原子递增 + 一个 worker 持锁异常退出后计数仍一致。"""
    mod = _load_fuzz()
    mod._lock_round(seed=11, workers=2, ops=3, crash_workers=1)  # 内部断言不符会抛错


@skip_windows_mp
def test_run_fuzz_with_lock_round():
    mod = _load_fuzz()
    results = mod.run_fuzz(http_iterations=10, lock_rounds=1, seed=3)
    assert results["crashes"] == 0
    assert results["target_crashes"]["lock_concurrency"] == 0


def test_check_thresholds_overflow():
    mod = _load_fuzz()
    results = {
        "crash_rate": 0.05,
        "target_crashes": {"sidecar_http": 2, "lock_concurrency": 0},
    }
    failures = mod.check_thresholds(results, max_crash_rate=0.0)
    assert len(failures) == 2
    assert any("sidecar_http" in item for item in failures)
    assert any("崩溃率" in item for item in failures)


def test_check_thresholds_all_pass():
    mod = _load_fuzz()
    results = {
        "crash_rate": 0.0,
        "target_crashes": {"sidecar_http": 0, "lock_concurrency": 0},
    }
    assert mod.check_thresholds(results) == []


def test_main_json_output(capsys):
    mod = _load_fuzz()
    rc = mod.main(["--json", "--http-iterations", "10", "--lock-rounds", "0", "--seed", "3"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["benchmark"] == "sidecar-fuzz"
    assert data["n_cases"] == 10
    assert data["crashes"] == 0


def test_main_fail_fast_accepts_clean():
    mod = _load_fuzz()
    rc = mod.main(["--fail-fast", "--http-iterations", "10", "--lock-rounds", "0", "--seed", "1"])
    assert rc == 0


def test_main_fail_fast_rejects_crash(monkeypatch):
    mod = _load_fuzz()
    bad = mod.run_fuzz(http_iterations=10, lock_rounds=0, seed=1)
    bad["crash_rate"] = 0.1
    bad["target_crashes"]["sidecar_http"] = 10
    monkeypatch.setattr(mod, "run_fuzz", lambda http_iterations=300, lock_rounds=3, seed=42: bad)
    rc = mod.main(["--fail-fast", "--http-iterations", "10", "--lock-rounds", "0", "--seed", "1"])
    assert rc == 1
