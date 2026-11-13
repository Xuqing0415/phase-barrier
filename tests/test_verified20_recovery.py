"""Recovery must preserve scored jobs and distinguish Agent behavior from transport faults."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checks = load("verified20_checks")
runner = load("run_swebench_batch_container")


@pytest.mark.parametrize("log,expected", [
    ("PB_DIFF_CHARS=0\n[turn 3] 模型错误: URLError: [Errno -2] Name or service not known", "model_call_error"),
    ("DNS resolution failed: api.example.test", "model_call_error"),
    ("ConnectionError: connection reset", "model_call_error"),
    ("TimeoutError: request timed out", "model_call_error"),
    ("[turn 3] 模型错误: HTTPError: 429", "model_call_error"),
    ("PB_STOP_REASON=agent_gave_up\nPB_DIFF_CHARS=0", "agent_gave_up"),
    ("[turn 1] assistant: done\n[turn 2] assistant: done\n[turn 3] assistant: done\nPB_DIFF_CHARS=0\nPB_SECONDS=1.0", "agent_gave_up"),
    ('[turn 2] execute_command -> {"output":"ConnectionError: timeout"}\nPB_DIFF_CHARS=0', "unknown"),
    ("PB_DIFF_CHARS=0\nPB_SECONDS=120.0", "unknown"),
    ("", "unknown"),
])
def test_empty_patch_cause(log, expected):
    assert checks.classify_empty_patch(log) == expected


def log_for(tmp_path, iid, text):
    (tmp_path / (iid.replace("__", "_") + "_baseline_verified20_formal.container.log")).write_text(text, encoding="utf-8")


def test_policy_stops_infra_not_agent_behavior_or_official_failure(tmp_path):
    row = {"instance_id": "x__x-1", "mode": "baseline", "note": "empty_patch"}
    log_for(tmp_path, row["instance_id"], "PB_STOP_REASON=agent_gave_up")
    assert not checks.monitoring_decision([row], tmp_path)["stop"]
    log_for(tmp_path, row["instance_id"], "[turn 3] 模型错误: DNS failed")
    assert checks.monitoring_decision([row], tmp_path)["reason"] == "model_call_error"
    log_for(tmp_path, row["instance_id"], "")
    assert checks.monitoring_decision([{**row, "note": "grade_infra_failure"}], tmp_path)["stop"]
    assert not checks.monitoring_decision([{**row, "note": "graded", "gate_completed": "1", "resolved": "0"}], tmp_path)["stop"]
    assert not checks.monitoring_decision([{**row, "note": "driver_error:other"}], tmp_path)["stop"]


def test_two_unknown_empty_patches_must_be_consecutive(tmp_path):
    a = {"instance_id": "x__x-1", "mode": "baseline", "note": "empty_patch"}
    b = {**a, "instance_id": "x__x-2"}
    assert not checks.monitoring_decision([a], tmp_path)["stop"]
    assert checks.monitoring_decision([a, b], tmp_path)["reason"] == "two_consecutive_unknown_empty_patches"
    assert not checks.monitoring_decision([a, {**a, "note": "graded"}, b], tmp_path)["stop"]


@pytest.mark.parametrize("note", ["model_call_error", "model_call_error empty_patch"])
def test_explicit_model_failure_stops_even_without_a_log(tmp_path, note):
    row = {"instance_id": "x__x-1", "mode": "baseline", "note": note}
    assert checks.monitoring_decision([row], tmp_path)["stop"]


def scored_job(root, iid, mode="baseline"):
    label = iid.replace("__", "_") + "_" + mode + "_verified20_formal"
    directory = root / "agent_runs" / label
    directory.mkdir(parents=True)
    patch = "diff --git a/x b/x\n"
    (directory / "model_patch.diff").write_text(patch)
    row = dict(instance_id=iid, repo="x/x", mode=mode, resolved="1", turns="2", seconds="1.0",
               diff_chars=str(len(patch)), gate_intercepts="0", gate_final_stage="1", gate_completed="0", note="graded")
    (directory / "stats.json").write_text(json.dumps(row))
    for folder in ("reports", "predictions"):
        (root / "eval_runs" / folder).mkdir(parents=True, exist_ok=True)
    (root / "eval_runs/reports" / (label + ".json")).write_text(json.dumps({iid: {
        "resolved": True, "patch_successfully_applied": True, "infra_failure": False}}))
    (root / "eval_runs/predictions" / (label + ".json")).write_text(json.dumps([{
        "instance_id": iid, "model_patch": patch}]))
    if mode == "gated":
        (directory / "state.json").write_text(json.dumps({"current_stage": 1}))
        (directory / "trace.jsonl").write_text(json.dumps({"event": "tool_call", "blocked": False,
                                                         "stage_before": 1, "stage_after": 1}) + "\n")
    return row


def write_csv(path, rows):
    import csv
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=runner.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_resume_partition_counts_jobs_not_pairs_and_verifies_patch(tmp_path):
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{"instance_id": f"x__x-{i}"} for i in range(1, 4)]))
    a = scored_job(tmp_path, "x__x-1")
    b = scored_job(tmp_path, "x__x-1", "gated")
    interrupted = tmp_path / "agent_runs/x_x-2_gated_verified20_formal"
    interrupted.mkdir()
    (interrupted / "model_patch.diff").write_text("saved patch")
    (interrupted / "stats.json").write_text(json.dumps({"instance_id": "x__x-2", "mode": "gated",
                                                        "diff_chars": len("saved patch")}))
    empty = {**a, "instance_id": "x__x-2", "note": "empty_patch", "diff_chars": "0"}
    results = tmp_path / "results.csv"
    write_csv(results, [a, b, empty])
    plan = checks.build_resume_plan(results, tasks)
    assert plan["passed"] and plan["completed_pairs"] == 1
    assert plan["counts"] == {"skip": 2, "regrade_only": 1, "rerun": 1, "pending": 2}
    assert sum(plan["counts"].values()) == 6
    (tmp_path / "agent_runs/x_x-1_baseline_verified20_formal/model_patch.diff").write_text("tampered")
    with pytest.raises(ValueError, match="Official evidence mismatch"):
        checks.build_resume_plan(results, tasks)


def test_duplicate_resume_rows_rejected(tmp_path):
    tasks = tmp_path / "tasks.json"
    tasks.write_text('[{"instance_id":"x__x-1"}]')
    row = scored_job(tmp_path, "x__x-1")
    results = tmp_path / "results.csv"
    write_csv(results, [row, row])
    with pytest.raises(ValueError, match="Duplicate"):
        checks.build_resume_plan(results, tasks)


def test_dns_flags_are_optional_and_repeatable():
    kwargs = dict(image="test:latest", mounts=[], agent_script="/agent.py", dataset="/data.json",
                  instance_id="x", mode="baseline", label="x", outdir="/out", testbed_python="python",
                  gate_python="python", max_turns=60)
    assert "--dns" not in runner.build_agent_docker_command(**kwargs)
    command = runner.build_agent_docker_command(**kwargs, dns_servers=["223.5.5.5", "1.1.1.1"])
    assert command[3:7] == ["--dns", "223.5.5.5", "--dns", "1.1.1.1"]


def test_runner_never_grades_model_transport_failure(monkeypatch, tmp_path):
    calls = []
    def fake_run(argv, timeout, log):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0), "PB_DIFF_CHARS=0\n[turn 3] 模型错误: DNS failure"
    monkeypatch.setattr(runner, "_run", fake_run)
    args = SimpleNamespace(label_suffix="_test", log_dir=tmp_path / "logs", agent_runs=tmp_path / "runs",
                           agent_script=tmp_path / "agent.py", repo_mount=ROOT, agent_dataset=tmp_path / "data.json",
                           testbed_python="python", gate_python="python", max_turns=60, exec_timeout=1,
                           agent_timeout=1, dns=["223.5.5.5"])
    row = runner.run_one({"instance_id": "x", "repo": "x/x", "mode": "baseline"}, args,
                         {"x": {"instance_id": "x", "image": "test:latest"}})
    assert row["note"] == "model_call_error empty_patch"
    assert row["resolved"] == "" and len(calls) == 1


def test_batch_stops_scheduling_after_model_failure(monkeypatch, tmp_path):
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{"instance_id": "x__x-1"}, {"instance_id": "x__x-2"}]))
    dataset = tmp_path / "dataset.json"
    dataset.write_bytes(tasks.read_bytes())
    called = []
    def fail(job, args, instances):
        called.append(job)
        return {**job, "note": "model_call_error", "resolved": ""}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder")
    monkeypatch.setattr(runner, "run_one", fail)
    results = tmp_path / "results.csv"
    monkeypatch.setattr(sys, "argv", ["runner", "--tasks", str(tasks), "--dataset", str(dataset),
                        "--results", str(results), "--agent-runs", str(tmp_path / "runs"),
                        "--modes", "baseline", "--concurrency", "1"])
    assert runner.main() == 3
    assert len(called) == 1
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "halted"


def test_runner_can_be_loaded_by_external_launcher_from_another_directory(tmp_path):
    path = ROOT / "scripts/run_swebench_batch_container.py"
    proc = subprocess.run([sys.executable, "-c", "import runpy; runpy.run_path(" + repr(str(path)) +
                           ", run_name='import_probe')"], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_timeout_keeps_live_output_including_unicode(tmp_path):
    log = tmp_path / "container.log"
    command = [sys.executable, "-u", "-c", "import time; print('模型调用诊断', flush=True); time.sleep(20)"]
    with pytest.raises(subprocess.TimeoutExpired):
        runner._run(command, 2, log)
    assert "模型调用诊断" in log.read_text(encoding="utf-8")
