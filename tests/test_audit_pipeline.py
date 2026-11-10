import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checks", ROOT / "scripts/verified20_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
FIXTURES = Path(__file__).parent / "fixtures/verified20_audit"


def pilot_row(name):
    data = json.loads((FIXTURES / f"{name}_pilot.json").read_text())
    return {**data["stats"], "resolved": int(data["report"]["resolved"])}


def test_real_pilot_outcome_classification_requires_explicit_review():
    astropy = pilot_row("astropy")
    assert checks.classify_gate_result(astropy)["failures"]
    review = {"instance_id": astropy["instance_id"], "classification": "gate_completed_insufficient",
              "approved": True}
    assert checks.classify_gate_result(astropy, review) == {
        "flags": ["gate_completed_insufficient"], "failures": []}
    review["instance_id"] = "different-instance"
    assert checks.classify_gate_result(astropy, review)["failures"]
    assert checks.classify_gate_result(pilot_row("django")) == {
        "flags": ["official_resolved_gate_incomplete"], "failures": []}


@pytest.mark.parametrize("name", ["astropy", "django"])
def test_real_container_trace_roundtrip_and_count_corruption(name, tmp_path):
    source = FIXTURES / f"{name}_new_trace_validation"
    assert checks.audit_persisted_run(source)["passed"]
    shutil.copytree(source, tmp_path / "run")
    stats_file = tmp_path / "run/stats.json"
    stats = json.loads(stats_file.read_text())
    stats["gate_intercepts"] += 1
    stats_file.write_text(json.dumps(stats))
    assert "interception_count_mismatch" in checks.audit_persisted_run(tmp_path / "run")["errors"]


def test_equal_or_older_test_timestamp_rejected(tmp_path):
    shutil.copytree(FIXTURES / "django_new_trace_validation", tmp_path / "run")
    directory = tmp_path / "run"
    stats = json.loads((directory / "stats.json").read_text())
    stats.update(gate_completed=1, gate_final_stage=6)
    (directory / "stats.json").write_text(json.dumps(stats))
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    state.update(current_stage=6, evidence={"last_test_run": {"passed": True, "at_epoch": 10},
                                           "last_source_change_at_epoch": 10})
    (directory / "state.json").write_text(json.dumps(state))
    assert "delivery_freshness_invalid" in checks.audit_persisted_run(directory)["errors"]
