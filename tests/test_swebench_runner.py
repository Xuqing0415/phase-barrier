"""真实 SWE-bench 评测 harness 冒烟测试（v0.41.0）。

覆盖：实例清单加载（list / dict 两种形态）、合成评测确定性、标记解析
（PB_RESOLVED / PB_GATE_INTERCEPTS）、真实子进程编排（假 Agent 命令）、
CLI JSON 输出与 --fail-fast 拒绝路径。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "pb_swebench_runner", ROOT / "benchmarks" / "swebench_runner.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pb_swebench_runner"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_runner()


def _write_instances(tmp_path, count=3):
    p = tmp_path / "instances.json"
    data = [
        {
            "instance_id": f"t{i:02d}",
            "repo": f"example/repo{i}",
            "base_commit": "a" * 40,
            "problem_statement": f"task {i}",
            "patch": "",
            "test_patch": "",
        }
        for i in range(count)
    ]
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------- 实例清单加载 ----------


def test_load_instances_list(mod, tmp_path):
    p = _write_instances(tmp_path, 3)
    out = mod._load_instances(p)
    assert [x["instance_id"] for x in out] == ["t00", "t01", "t02"]


def test_load_instances_dict_wrapper(mod, tmp_path):
    p = tmp_path / "wrapped.json"
    p.write_text(
        json.dumps({"instances": [{"instance_id": "x1"}]}), encoding="utf-8"
    )
    assert [x["instance_id"] for x in mod._load_instances(p)] == ["x1"]


def test_load_instances_empty_fails(mod, tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps([{"repo": "no-id"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        mod._load_instances(p)


# ---------- 标记解析 ----------


def test_parse_markers(mod):
    out = "PB_RESOLVED=1\nPB_GATE_INTERCEPTS=3\nsome log\n"
    assert mod._parse_markers(out) == (True, 3)
    out2 = "PB_RESOLVED=0\n"
    assert mod._parse_markers(out2) == (False, 0)
    # 无标记 -> resolved=None（回退退出码）
    assert mod._parse_markers("hello") == (None, 0)


# ---------- 合成评测 ----------


def test_synthetic_deterministic_and_aggregates(mod, tmp_path):
    instances = mod._synthetic_instances(50, 7)
    a = mod.run_benchmark(instances, tmp_path, None, None, seed=7, timeout=600, synthetic=True)
    b = mod.run_benchmark(instances, tmp_path, None, None, seed=7, timeout=600, synthetic=True)
    assert a["baseline"] == b["baseline"]
    assert a["gated"] == b["gated"]
    assert a["summary"] == b["summary"]
    assert a["n_instances"] == 50
    assert a["baseline"]["runs"] == 50 and a["gated"]["runs"] == 50
    assert 0.0 <= a["baseline"]["resolve_rate"] <= 1.0
    assert a["gated"]["resolve_rate"] >= a["baseline"]["resolve_rate"]
    assert a["gated"]["gate_intercepts"] > 0
    assert a["summary"]["gated_resolve_above_baseline"] is True
    assert a["summary"]["gated_intercepted_any"] is True


def test_check_thresholds(mod):
    report = {
        "baseline": {"runs": 10, "resolve_rate": 0.9, "gate_intercepts": 0},
        "gated": {"runs": 10, "resolve_rate": 0.8, "gate_intercepts": 5},
    }
    failures = mod.check_thresholds(report)
    assert any("低于" in f for f in failures)
    report2 = {
        "baseline": {"runs": 10, "resolve_rate": 0.5},
        "gated": {"runs": 10, "resolve_rate": 0.8, "gate_intercepts": 0},
    }
    assert any("拦截数为 0" in f for f in mod.check_thresholds(report2))
    assert mod.check_thresholds(report2, require_gated_intercepts=False) == []


# ---------- CLI ----------


def test_main_synthetic_json(mod, capsys):
    rc = mod.main(["--synthetic", "10", "--seed", "3", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["benchmark"] == "swebench-real-harness"
    assert data["n_instances"] == 10 and data["synthetic"] is True


def test_main_synthetic_fail_fast_accepts(mod):
    assert mod.main(["--synthetic", "20", "--seed", "1", "--fail-fast"]) == 0


def test_main_fail_fast_rejects(monkeypatch, mod, capsys):
    bad = mod.run_benchmark(
        mod._synthetic_instances(5, 1), Path("."), None, None, seed=1, timeout=600, synthetic=True
    )
    monkeypatch.setattr(
        mod, "check_thresholds", lambda report, require_gated_intercepts=True: ["synthetic failure"]
    )
    rc = mod.main(["--synthetic", "5", "--seed", "1", "--fail-fast"])
    assert rc == 1
    assert "synthetic failure" in capsys.readouterr().err


def test_main_real_requires_cmd_templates(mod, tmp_path):
    p = _write_instances(tmp_path)
    with pytest.raises(SystemExit):
        mod.main(["--instances", str(p)])


def test_main_real_run_with_fake_agents(mod, tmp_path):
    p = _write_instances(tmp_path, 2)
    report_path = tmp_path / "report.json"
    rc = mod.main(
        [
            "--instances",
            str(p),
            "--cmd-baseline",
            'python -c "import sys; sys.stdout.write(\'PB_RESOLVED=1\\n\')"',
            "--cmd-gated",
            'python -c "import sys; sys.stdout.write(\'PB_RESOLVED=0\\nPB_GATE_INTERCEPTS=2\\n\')"',
            "--work-root",
            str(tmp_path / "work"),
            "--output",
            str(report_path),
        ]
    )
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["baseline"]["resolved"] == 2
    assert report["gated"]["resolved"] == 0
    assert report["gated"]["gate_intercepts"] == 4