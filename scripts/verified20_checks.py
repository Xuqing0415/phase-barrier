"""Outcome annotations and stale-artifact guards for the preregistered batch."""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

REUSED_INSTANCES = ("mwaskom__seaborn-3069", "pallets__flask-5014")


def _log_text(instance_log):
    if isinstance(instance_log, Path):
        return instance_log.read_text(encoding="utf-8", errors="replace") if instance_log.exists() else ""
    return str(instance_log or "")


def model_call_failed(instance_log):
    """Recognize model transport errors, not errors quoted in shell/test output."""
    for line in _log_text(instance_log).splitlines():
        if " -> " in line or re.match(r"^\[turn \d+\] assistant:", line):
            continue
        if re.search(r"模型错误\s*[:：]|模型调用失败|^PB_STOP_REASON=model_call_error$|"
                     r"^PB_MODEL_CALL_ERROR=1$", line):
            return True
        if re.match(r"^(?:DNS resolution failed|ConnectionError:|TimeoutError:|"
                    r"(?:Model|API|LLM) (?:call|request) (?:failed|timeout))", line, re.I):
            return True
    return False


def classify_empty_patch(instance_log):
    """Classify empty output conservatively using explicit runner evidence."""
    text = _log_text(instance_log)
    if model_call_failed(text):
        return "model_call_error"
    if re.search(r"^PB_STOP_REASON=agent_gave_up$", text, re.M):
        return "agent_gave_up"
    # Legacy runner stops after three consecutive assistant-only replies.
    replies = 0
    for line in text.splitlines():
        if re.match(r"^\[turn \d+\] assistant:", line):
            replies += 1
        elif re.match(r"^\[turn \d+\]", line):
            replies = 0
    if (replies >= 3 and re.search(r"^PB_DIFF_CHARS=0$", text, re.M)
            and re.search(r"^PB_SECONDS=\d", text, re.M)):
        return "agent_gave_up"
    return "unknown"


def monitoring_decision(rows, log_dir, label_suffix="_verified20_formal", *, stop_on_unknown_empty=True):
    """Apply the ordered root-cause policy to completed rows."""
    unknown_streak = 0
    classifications = []
    for row in rows:
        label = row["instance_id"].replace("__", "_") + "_" + row["mode"] + label_suffix
        log = Path(log_dir) / (label + ".container.log")
        note = row.get("note", "")
        cause = classify_empty_patch(log) if "empty_patch" in note.split() else None
        classifications.append({"instance_id": row["instance_id"], "mode": row["mode"],
                                "note": note, "empty_patch_cause": cause})
        if "grade_infra_failure" in note.split():
            return {"stop": True, "reason": "grade_infra_failure", "classifications": classifications}
        if "model_call_error" in note.split() or model_call_failed(log):
            return {"stop": True, "reason": "model_call_error", "classifications": classifications}
        unknown_streak = unknown_streak + 1 if cause == "unknown" else 0
        if stop_on_unknown_empty and unknown_streak >= 2:
            return {"stop": True, "reason": "two_consecutive_unknown_empty_patches",
                    "classifications": classifications}
    return {"stop": False, "reason": None, "classifications": classifications}


def build_resume_plan(results, tasks):
    """Verify official evidence and partition unique jobs without counting pairs twice."""
    results = Path(results)
    root = results.parent
    raw = json.loads(Path(tasks).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = next(raw[k] for k in ("instances", "tasks", "rows", "data") if isinstance(raw.get(k), list))
    expected = [(r["instance_id"], mode) for r in raw for mode in ("baseline", "gated")]
    if len(expected) != len(set(expected)):
        raise ValueError("Duplicate manifest jobs")
    with results.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_key = {}
    for row in rows:
        key = (row["instance_id"], row["mode"])
        if key in by_key or key not in expected:
            raise ValueError("Duplicate or unexpected result: " + str(key))
        by_key[key] = row
    plan = {"skip": [], "regrade_only": [], "rerun": [], "pending": []}
    for iid, mode in expected:
        record = {"instance_id": iid, "mode": mode}
        label = iid.replace("__", "_") + "_" + mode + "_verified20_formal"
        directory = root / "agent_runs" / label
        patch = directory / "model_patch.diff"
        stats_path = directory / "stats.json"
        row = by_key.get((iid, mode))
        if row and row["note"] == "graded":
            report = json.loads((root / "eval_runs/reports" / (label + ".json")).read_text(encoding="utf-8"))[iid]
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            patch_text = patch.read_text(encoding="utf-8")
            prediction = json.loads((root / "eval_runs/predictions" / (label + ".json")).read_text(encoding="utf-8"))[0]
            if (not report.get("patch_successfully_applied") or report.get("infra_failure")
                    or int(bool(report["resolved"])) != int(row["resolved"])
                    or prediction["model_patch"] != patch_text or prediction["instance_id"] != iid
                    or len(patch_text) != int(row["diff_chars"])):
                raise ValueError("Official evidence mismatch: " + label)
            for key in ("turns", "seconds", "diff_chars", "gate_intercepts", "gate_completed"):
                if float(stats[key]) != float(row[key]):
                    raise ValueError("Stats mismatch: " + label + "/" + key)
            if mode == "gated" and not audit_persisted_run(directory)["passed"]:
                raise ValueError("Audit evidence mismatch: " + label)
            record["patch_sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
            record["resolved"] = int(row["resolved"])
            plan["skip"].append(record)
        elif patch.exists() and patch.stat().st_size and stats_path.exists():
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            if (stats.get("instance_id") != iid or stats.get("mode") != mode
                    or stats.get("diff_chars") != len(patch.read_text(encoding="utf-8"))):
                raise ValueError("Saved patch metadata mismatch: " + label)
            record.update(patch_file=str(patch), patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                          prior_model_call_error=model_call_failed(root / "logs" / (label + ".container.log")))
            plan["regrade_only"].append(record)
        elif row or directory.exists():
            record["reason"] = classify_empty_patch(root / "logs" / (label + ".container.log"))
            plan["rerun"].append(record)
        else:
            plan["pending"].append(record)
    done = {(r["instance_id"], r["mode"]) for r in plan["skip"]}
    plan["completed_pairs"] = sum((iid, "baseline") in done and (iid, "gated") in done
                                    for iid in {iid for iid, _ in expected})
    plan["counts"] = {key: len(plan[key]) for key in ("skip", "regrade_only", "rerun", "pending")}
    plan["total_jobs"] = len(expected)
    plan["passed"] = sum(plan["counts"].values()) == len(expected)
    return plan


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


def main():
    parser = argparse.ArgumentParser(description="Verify completed official results before resume")
    parser.add_argument("--verify-completed", action="store_true", required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=Path(__file__).resolve().parents[1] /
                        "benchmarks/swebench/manifests/verified20-preregistered-2026-11-08.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_resume_plan(args.results, args.tasks)
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": plan["passed"], **plan["counts"],
                      "completed_pairs": plan["completed_pairs"], "total_jobs": plan["total_jobs"]}))
    return 0 if plan["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
