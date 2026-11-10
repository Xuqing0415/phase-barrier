"""Outcome annotations and stale-artifact guards for the preregistered batch."""
from pathlib import Path

REUSED_INSTANCES = ("mwaskom__seaborn-3069", "pallets__flask-5014")


def classify_gate_result(row):
    """Keep solve outcomes separate from gate completion and infrastructure."""
    flags, failures = [], []
    if int(row["diff_chars"]) >= 100_000:
        flags.append("large_patch")
    if row["mode"] == "gated":
        completed, resolved = int(row["gate_completed"]), int(row["resolved"])
        if completed == 0 and resolved == 1:
            flags.append("official_resolved_gate_incomplete")
        if completed == 1 and resolved == 0:
            failures.append("gate_complete_official_unresolved")
        if completed == 0 and str(row["gate_final_stage"]) in ("2", "3"):
            flags.append("early_stage_requires_analysis")
    return {"flags": flags, "failures": failures}


def check_reused_patch_dirs(agent_runs, label_suffix="_verified20_formal"):
    """Reject stale files in the actual runner output paths; never erase history."""
    root = Path(agent_runs).resolve()
    records = []
    for iid in REUSED_INSTANCES:
        for mode in ("baseline", "gated"):
            directory = root / (iid.replace("__", "_") + "_" + mode + label_suffix)
            resolved = directory.resolve()
            if not resolved.is_relative_to(root) or directory.is_symlink():
                raise RuntimeError(f"Unsafe patch directory: {directory}")
            if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
                raise RuntimeError(f"{iid}/{mode}: patch directory is not empty: {directory}")
            records.append({"instance_id": iid, "mode": mode, "directory": str(directory),
                            "patch_file": str(directory / "model_patch.diff"),
                            "absent_or_empty": True})
    return {"passed": True, "directories": records}
