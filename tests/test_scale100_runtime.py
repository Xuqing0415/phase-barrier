"""Scale-100 progress counts must count complete official pairs."""
import csv
import json
import hashlib
import subprocess
import pytest

from scripts import scale100_runtime as runtime


def test_snapshot_does_not_confuse_distinct_instances_with_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUN_ROOT", tmp_path)
    with (tmp_path / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "instance_id", "mode", "note", "gate_intercepts", "gate_completed", "resolved", "seconds"])
        writer.writeheader()
        for iid, mode, note in [
            ("a__a-1", "baseline", "graded"), ("a__a-1", "gated", "graded"),
            ("b__b-1", "baseline", "graded"), ("c__c-1", "gated", "model_call_error"),
        ]:
            writer.writerow({"instance_id": iid, "mode": mode, "note": note,
                             "gate_intercepts": 0, "gate_completed": 0,
                             "resolved": 0, "seconds": 1})
    out = tmp_path / "agent_runs" / "a_a-1_gated_scale100_20261114"
    out.mkdir(parents=True)
    (out / "stats.json").write_text(json.dumps({"mode": "gated", "instance_id": "a__a-1",
                                               "test_preservation_intercepts": 2}), encoding="utf-8")
    result = runtime.snapshot("running")
    assert result["completed_jobs"] == 4
    assert result["officially_graded_jobs"] == 3
    assert result["completed_pairs"] == 1
    assert result["test_preservation_intercepts"] == 2
    assert json.loads((tmp_path / "monitor_status.json").read_text(encoding="utf-8")) == result


def test_stop_only_cleans_exact_grader_names_for_saved_predictions(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUN_ROOT", tmp_path)
    predictions = tmp_path / "eval_runs/predictions"
    predictions.mkdir(parents=True)
    patch = "diff --git a/x b/x\n"
    label = "a_a-1_baseline_scale100_20261114"
    (predictions / (label + ".json")).write_text(json.dumps([
        {"instance_id": "a__a-1", "model_patch": patch}
    ]), encoding="utf-8")
    (tmp_path / "status.json").write_text('{"state":"halted"}', encoding="utf-8")
    name = "sweb.eval.a__a-1." + label + "-" + hashlib.sha256(patch.encode()).hexdigest()[:12]
    monkeypatch.setattr(runtime, "stop_verified_batch", lambda *a, **kw: {
        "cleanup_errors": [], "removed_containers": [], "runner_alive_after_stop": False,
    })
    called = []

    def run(argv, **kwargs):
        called.append(argv)
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, f"ours {name}\nother postgres-skill\nwrong {name}-unrelated\n")
        return subprocess.CompletedProcess(argv, 0, "")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    result = runtime.stop_batch(100, "model_call_error")
    assert result["cleanup_complete"]
    assert called == [
        ["docker", "ps", "--format", "{{.ID}} {{.Names}}"], ["docker", "rm", "-f", "ours"],
    ]


def test_testbed_checks_tree_content_instead_of_setup_commit_identity(monkeypatch):
    actual = {"head": "setup-commit", "head_tree": "same-tree", "base_tree": "same-tree",
              "status": "", "testbed_python": "Python 3.9.0"}
    monkeypatch.setattr(runtime.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, json.dumps(actual)))
    task = {"instance_id": "a__a-1", "image": "logicstar/a:latest", "base_commit": "base-commit"}
    assert runtime.inspect_testbed(task)["passed"]
    actual["status"] = " M source.py"
    assert not runtime.inspect_testbed(task)["passed"]
    actual["status"] = ""
    actual["head_tree"] = "different-tree"
    assert not runtime.inspect_testbed(task)["passed"]


def test_reviewed_testbed_audit_rejects_unreviewed_drift(tmp_path, monkeypatch):
    path = tmp_path / "audit.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runtime, "TESTBED_AUDIT", path)
    checks = [{"instance_id": str(i), "image": "image", "base_commit": "base",
               "actual": {"head_tree": "reviewed-setup-tree", "status": ""}, "returncode": 0}
              for i in range(100)]
    audit = {"passed": True, "records": [{**row, "approved": True} for row in checks]}
    assert runtime.verify_testbed_records(checks, audit)["passed"]
    checks[0] = {**checks[0], "actual": {"head_tree": "new-tree", "status": ""}}
    with pytest.raises(ValueError, match="differs from reviewed"):
        runtime.verify_testbed_records(checks, audit)
