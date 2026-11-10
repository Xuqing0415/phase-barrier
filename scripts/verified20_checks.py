"""Outcome annotations and stale-artifact guards for the preregistered batch."""
import json
from pathlib import Path

REUSED_INSTANCES = ("mwaskom__seaborn-3069", "pallets__flask-5014")


def classify_gate_result(row, review=None):
    """Keep solve outcomes separate from gate completion and infrastructure."""
    flags, failures = [], []
    if int(row["diff_chars"]) >= 100_000:
        flags.append("large_patch")
    if row["mode"] == "gated":
        completed, resolved = int(row["gate_completed"]), int(row["resolved"])
        if completed == 0 and resolved == 1:
            flags.append("official_resolved_gate_incomplete")
        if completed == 1 and resolved == 0:
            if (review and review.get("instance_id") == row.get("instance_id")
                    and review.get("classification") == "gate_completed_insufficient"
                    and review.get("approved") is True):
                flags.append("gate_completed_insufficient")
            else:
                failures.append("gate_complete_official_unresolved")
        if completed == 0 and str(row["gate_final_stage"]) in ("2", "3"):
            flags.append("early_stage_requires_analysis")
    return {"flags": flags, "failures": failures}


def audit_persisted_run(directory):
    """Validate saved runner events and delivery freshness without inventing history."""
    directory = Path(directory)
    stats = json.loads((directory / "stats.json").read_text(encoding="utf-8"))
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (directory / "trace.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    errors = []
    if not events:
        errors.append("missing_tool_events")
    for event in events:
        if type(event.get("blocked")) is not bool or event.get("event") != (
                "blocked" if event.get("blocked") else "tool_call"):
            errors.append("invalid_blocked_event")
        before, after = event["stage_before"], event["stage_after"]
        if before != after and not (after == before + 1 or (before, after) == (4, 6)):
            errors.append("invalid_stage_transition")
    for before, after in zip(events, events[1:]):
        if before["stage_after"] != after["stage_before"]:
            errors.append("discontinuous_stage_trace")
    blocked = sum(event.get("blocked") is True for event in events)
    if blocked != int(stats["gate_intercepts"]):
        errors.append("interception_count_mismatch")
    if events and events[-1]["stage_after"] != state["current_stage"]:
        errors.append("state_trace_stage_mismatch")
    if state["current_stage"] != int(stats["gate_final_stage"]):
        errors.append("state_stats_stage_mismatch")
    if int(stats["gate_completed"]):
        evidence = state.get("evidence", {})
        run = evidence.get("last_test_run", {})
        changed = evidence.get("last_source_change_at_epoch")
        ran = run.get("at_epoch")
        if (state["current_stage"] < 6 or not run.get("passed") or ran is None
                or (changed is not None and ran <= changed)):
            errors.append("delivery_freshness_invalid")
    return {"passed": not errors, "errors": errors, "events": len(events), "blocked": blocked}


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
