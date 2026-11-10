import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verified20_checks", Path(__file__).resolve().parents[1] / "scripts/verified20_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.mark.parametrize("completed,resolved,flags,failures", [
    (0, 1, ["official_resolved_gate_incomplete"], []),
    (1, 0, [], ["gate_complete_official_unresolved"]),
    (0, 0, [], []),
    (1, 1, [], []),
])
def test_gate_and_official_outcomes(completed, resolved, flags, failures):
    row = dict(mode="gated", gate_completed=completed, resolved=resolved,
               diff_chars=4972, gate_final_stage=4)
    assert checks.classify_gate_result(row) == dict(flags=flags, failures=failures)


def test_guard_uses_actual_runner_labels_and_preserves_stale_patch(tmp_path):
    proof = checks.check_reused_patch_dirs(tmp_path)
    assert len(proof["directories"]) == 4
    patch = tmp_path / "pallets_flask-5014_gated_verified20_formal" / "model_patch.diff"
    patch.parent.mkdir()
    patch.write_text("old patch")
    with pytest.raises(RuntimeError, match="not empty"):
        checks.check_reused_patch_dirs(tmp_path)
    assert patch.read_text() == "old patch"


def test_empty_dirs_allowed_but_stale_stats_rejected(tmp_path):
    directory = tmp_path / "mwaskom_seaborn-3069_baseline_verified20_formal"
    directory.mkdir()
    assert checks.check_reused_patch_dirs(tmp_path)["passed"]
    (directory / "stats.json").write_text("{}")
    with pytest.raises(RuntimeError, match="not empty"):
        checks.check_reused_patch_dirs(tmp_path)
