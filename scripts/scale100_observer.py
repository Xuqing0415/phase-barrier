"""Record hourly, observational Scale-100 reviews without altering the run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from benchmarks.swebench.run_status import pid_is_alive

INTERVAL_SECONDS = 3600
DEFAULT_ROOT = REPO / ".pytest_tmp/scale100_20261114"


def read_json(path: Path, *, optional: bool = False) -> dict:
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if optional:
                return {}
            raise
        except json.JSONDecodeError:
            if attempt == 9:
                raise
            time.sleep(0.1)
    raise AssertionError("unreachable")


def save_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def preservation_events(root: Path) -> list[dict]:
    """Retain the full persisted rejection reason, including any test names."""
    records = []
    manifest = REPO / "benchmarks/swebench/manifests/scale100-verified-2026-11-14.json"
    ids = {row["instance_id"].replace("__", "_"): row["instance_id"]
           for row in read_json(manifest).get("tasks", [])} if manifest.is_file() else {}
    for path in sorted((root / "agent_runs").glob("*_gated_*/trace.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if number == len(lines):
                    continue  # The active writer may not have finished its final line.
                raise ValueError("malformed persisted trace: " + path.relative_to(root).as_posix())
            error = str((event.get("outcome") or {}).get("error", ""))
            if (event.get("blocked") is True and event.get("tool") == "advance_stage"
                    and "test_preservation strict" in error):
                records.append({"instance_id": ids.get(path.parent.name.split("_gated_", 1)[0]),
                                "label": path.parent.name, "turn": event.get("turn"),
                                "tool_call_id": event.get("tool_call_id"),
                                "source": path.relative_to(root).as_posix(), "line": number,
                                "rejection_reason": error,
                                "missing_tests_reported_in_error": re.findall(r"[\w./-]+::[\w.]+", error)})
    return records


def review(root: Path, *, previous: dict | None = None,
           now: float | None = None, pid_probe=pid_is_alive) -> dict:
    now = time.time() if now is None else now
    previous = previous or {}
    launch = read_json(root / "launch.json")
    monitor = read_json(root / "monitor_status.json")
    status = read_json(root / "status.json")
    halt = read_json(root / "halt.json", optional=True)
    runner_alive = pid_probe(launch["runner_pid"])
    supervisor_alive = pid_probe(launch["supervisor_pid"])
    reason = monitor.get("halt_reason") or halt.get("reason") or status.get("halt_reason")
    alerts = []
    state = monitor["state"]
    review_state = state
    if state == "running":
        if not runner_alive:
            review_state = "stale"
            alerts.append("runner_dead_status_running")
        if not supervisor_alive:
            review_state = "stale"
            alerts.append("supervisor_dead_status_running")
    if status.get("runner_pid") != launch["runner_pid"]:
        alerts.append("runner_pid_mismatch")
    if reason:
        alerts.append("saved_halt_requires_review")
    last_progress = previous.get("last_progress_observed_epoch", now)
    if monitor["completed_jobs"] != previous.get("completed_jobs"):
        last_progress = now
    if state == "running" and now - last_progress >= 2 * INTERVAL_SECONDS:
        alerts.append("completed_jobs_unchanged_for_two_hours")
    changed = [relative for relative, expected in launch["source_hashes"].items()
               if not (REPO / relative).is_file()
               or hashlib.sha256((REPO / relative).read_bytes()).hexdigest() != expected]
    if changed:
        alerts.append("frozen_source_changed")
    events = preservation_events(root)
    if events and not previous.get("preservation_trace_events"):
        alerts.append("first_test_preservation_rejection_observed")
    if monitor.get("test_preservation_intercepts", 0) > 0 and not events:
        alerts.append("preservation_counter_without_persisted_names_requires_review")
    terminal = (state.startswith("halted") or state in {
        "interrupted_review_required", "verified_complete_pending_report_review",
        "completion_evidence_review_required"} or review_state == "stale")
    if state == "verified_complete_pending_report_review":
        alerts.append("completion_requires_anomaly_and_report_review")
    return {"checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "review_kind": "automated_hourly_observation", "state": review_state,
            "monitor_state": state, "monitor_checked_at": monitor.get("checked_at"),
            "completed_jobs": monitor["completed_jobs"], "completed_pairs": monitor["completed_pairs"],
            "officially_graded_jobs": monitor["officially_graded_jobs"],
            "test_preservation_intercepts": monitor["test_preservation_intercepts"],
            "halt_reason": reason, "halt_detail": halt or None,
            "runner_pid": launch["runner_pid"], "supervisor_pid": launch["supervisor_pid"],
            "runner_alive": runner_alive, "supervisor_alive": supervisor_alive,
            "source_hashes_match": not changed, "changed_source_files": changed,
            "last_progress_observed_epoch": last_progress,
            "preservation_trace_events": events,
            "anomalies": {key: monitor.get(key, []) for key in (
                "zero_interception", "gate_completed_official_unresolved", "over_60_minutes")},
            "alerts": alerts, "terminal_observation": terminal,
            "next_review_at": None if terminal else datetime.fromtimestamp(now + INTERVAL_SECONDS, timezone.utc).isoformat(),
            "human_review_required": bool(alerts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("once", "watch"))
    parser.add_argument("--run-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.run_root.resolve()
    out = root / "hourly_review"
    out.mkdir(exist_ok=True)
    existing = read_json(out / "observer_launch.json", optional=True)
    if args.action == "watch" and existing.get("observer_pid") and pid_is_alive(existing["observer_pid"]):
        raise RuntimeError("an hourly observer is already alive")
    if args.action == "watch":
        save_json(out / "observer_launch.json", {"observer_pid": os.getpid(),
                  "started_at": datetime.now(timezone.utc).isoformat(), "interval_seconds": INTERVAL_SECONDS,
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        previous = read_json(out / "latest.json", optional=True)
        if previous.get("next_review_at"):
            first = datetime.fromisoformat(previous["next_review_at"]).timestamp()
            while time.time() < first:
                time.sleep(min(60, max(0.1, first - time.time())))
    while True:
        previous = read_json(out / "latest.json", optional=True)
        try:
            result = review(root, previous=previous)
        except Exception as exc:
            result = {**previous, "checked_at": datetime.now(timezone.utc).isoformat(),
                      "review_kind": "automated_hourly_observation", "state": "observer_error",
                      "alerts": ["observer_read_failed:" + type(exc).__name__],
                      "error_detail": str(exc), "human_review_required": True,
                      "terminal_observation": False,
                      "next_review_at": datetime.fromtimestamp(time.time() + INTERVAL_SECONDS, timezone.utc).isoformat()}
        with (out / "history.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        save_json(out / "latest.json", result)
        if result["alerts"]:
            save_json(out / "attention_required.json", result)
        print(json.dumps({key: result.get(key) for key in (
            "checked_at", "completed_jobs", "completed_pairs", "test_preservation_intercepts",
            "halt_reason", "state", "alerts", "next_review_at")}), flush=True)
        if args.action == "once" or result["terminal_observation"]:
            return 0
        # Only the clock is checked while waiting; no experiment file is polled.
        deadline = time.monotonic() + INTERVAL_SECONDS
        while time.monotonic() < deadline:
            time.sleep(min(60, max(0.1, deadline - time.monotonic())))


if __name__ == "__main__":
    raise SystemExit(main())
