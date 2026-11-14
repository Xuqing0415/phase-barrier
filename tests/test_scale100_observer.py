import json

from scripts import scale100_observer as observer


def setup_run(path):
    path.mkdir()
    for name, data in {
        "launch.json": {"runner_pid": 10, "supervisor_pid": 20, "source_hashes": {}},
        "status.json": {"state": "running", "runner_pid": 10},
        "monitor_status.json": {"state": "running", "completed_jobs": 9, "completed_pairs": 4,
                                "officially_graded_jobs": 9, "test_preservation_intercepts": 0},
    }.items():
        (path / name).write_text(json.dumps(data), encoding="utf-8")
    return path


def test_missing_monitor_halt_field_is_resolved_from_durable_halt_record(tmp_path):
    root = setup_run(tmp_path / "run")
    (root / "halt.json").write_text('{"reason":"model_call_error"}', encoding="utf-8")
    result = observer.review(root, now=100, pid_probe=lambda _: True)
    assert result["halt_reason"] == "model_call_error"
    assert result["human_review_required"]


def test_dead_runner_marks_observer_stale_without_rewriting_run(tmp_path):
    root = setup_run(tmp_path / "run")
    original = (root / "status.json").read_bytes()
    result = observer.review(root, now=100, pid_probe=lambda pid: pid != 10)
    assert result["state"] == "stale" and result["terminal_observation"]
    assert "runner_dead_status_running" in result["alerts"]
    assert (root / "status.json").read_bytes() == original


def test_two_hours_unchanged_only_flags_attention(tmp_path):
    root = setup_run(tmp_path / "run")
    result = observer.review(root, previous={"completed_jobs": 9, "last_progress_observed_epoch": 100},
                             now=7300, pid_probe=lambda _: True)
    assert result["state"] == "running" and not result["terminal_observation"]
    assert "completed_jobs_unchanged_for_two_hours" in result["alerts"]


def test_preservation_reason_retains_test_name_and_location(tmp_path):
    root = setup_run(tmp_path / "run")
    trace = root / "agent_runs/a_a-1_gated_scale100_20261114/trace.jsonl"
    trace.parent.mkdir(parents=True)
    reason = "test_preservation strict：阶段 2 测试缺失：test_repro.py::test_kept；请恢复"
    trace.write_text(json.dumps({"tool": "advance_stage", "blocked": True, "turn": 3,
                                "outcome": {"error": reason}}) + "\n{", encoding="utf-8")
    result = observer.review(root, now=100, pid_probe=lambda _: True)
    assert result["preservation_trace_events"][0]["rejection_reason"] == reason
    assert result["preservation_trace_events"][0]["line"] == 1
    assert "first_test_preservation_rejection_observed" in result["alerts"]
