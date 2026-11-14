"""Recheck the completed batch without modifying its original evidence.

This summarizes observed events; it does not execute saved tool commands or
infer semantic correctness from an interception counter.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("anomaly_checks", Path(__file__).with_name("verified20_checks.py"))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def read_json(path):
    return Path(path).read_text(encoding="utf-8-sig")


def audit(root, tasks, sleep_events):
    root = Path(root)
    verification = checks.build_resume_plan(root / "results.csv", tasks)
    if verification["counts"]["skip"] != 40 or verification["completed_pairs"] != 20:
        raise ValueError("Expected 40 verified official jobs and 20 complete pairs")
    rows = list(csv.DictReader((root / "results.csv").open(encoding="utf-8", newline="")))
    sources = {}
    zero, incomplete = [], []
    for row in rows:
        if row["mode"] != "gated":
            continue
        label = row["instance_id"].replace("__", "_") + "_gated_verified20_formal"
        directory = root / "agent_runs" / label
        paths = [directory / name for name in ("state.json", "stats.json", "trace.jsonl", "model_patch.diff")]
        paths.append(root / "logs" / (label + ".container.log"))
        for path in paths:
            sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        state = json.loads(read_json(paths[0]))
        events = [json.loads(line) for line in read_json(paths[2]).splitlines() if line.strip()]
        log = read_json(paths[-1])
        calls = [(int(turn), tool) for turn, tool in re.findall(r"^\[turn (\d+)\] (\w+) -> ", log, re.M)]
        complete_log = calls == [(e["turn"], e["tool"]) for e in events]
        record = {"instance_id": row["instance_id"], "turns": int(row["turns"]),
                  "stage": state["current_stage"], "events": len(events),
                  "blocked": sum(e["blocked"] for e in events), "diff_chars": int(row["diff_chars"]),
                  "patch_bytes": paths[3].stat().st_size, "tool_log_matches_trace": complete_log,
                  "history": [e["stage"] for e in state["stage_history"]] + [state["current_stage"]],
                  "persisted_audit": checks.audit_persisted_run(directory)}
        if not int(row["gate_intercepts"]):
            # All three traces contain a stage-2 pytest invocation. The source
            # classifier misses the preceding `cd ... &&` shell segment.
            premature = [{"turn": e["turn"], "stage": e["stage_before"],
                          "command": e["args"]["command"], "blocked": e["blocked"]}
                         for e in events if e["tool"] == "execute_command"
                         and e["stage_before"] < 3
                         and re.search(r"\bpython\s+-m\s+pytest\s+test_pb_repro\.py\b", e["args"].get("command", ""))]
            record.update(classification="zero_interception_suspicious" if premature or not complete_log
                          else "zero_interception_benign", premature_pytest=premature,
                          mechanism="shell_test_command_not_recognized" if premature else "no_observed_illegal_call",
                          logging_conclusion="complete_for_stdout_tool_calls" if complete_log else "mismatch_requires_review")
            zero.append(record)
        if int(row["resolved"]) and not int(row["gate_completed"]):
            record["classification"] = ("early_stage_incomplete" if record["stage"] in (2, 3)
                                        else "budget_exhausted_at_late_stage"
                                        if record["stage"] in (4, 5) and record["turns"] >= 53
                                        else "unclassified")
            record["stage_rejections"] = [{"turn": e["turn"], "error": e.get("outcome", {}).get("error")}
                                          for e in events if e["tool"] == "advance_stage" and e["blocked"]]
            incomplete.append(record)
    sleep = json.loads(read_json(sleep_events))
    if len(sleep) != 1 or not sleep[0]["data"]["SleepTime"].startswith("2026-11-13T03:59:40"):
        raise ValueError("Unexpected host-sleep evidence; review its identity")
    runner_log = read_json(root / "runner.log")
    long = []
    for mode, start, end in (("baseline", "11:55:07", "14:26:23"), ("gated", "11:58:27", "14:25:25")):
        iid = "sympy__sympy-18211"
        for action, clock in (("run ", start), ("grade", end)):
            if f"[{clock}] [{action}] {iid} {mode}" not in runner_log:
                raise ValueError("Missing run/grade boundary")
        row = next(r for r in rows if r["instance_id"] == iid and r["mode"] == mode)
        long.append({"instance_id": iid, "mode": mode, "seconds": float(row["seconds"]),
                     "classification": "cross_interruption", "excluded_from_efficiency": True,
                     "run_local": "2026-11-13T" + start + "+08:00",
                     "grade_local": "2026-11-13T" + end + "+08:00"})
    counts = {"gate_complete_official_unresolved": sum(r["mode"] == "gated" and r["gate_completed"] == "1" and r["resolved"] == "0" for r in rows),
              "official_resolved_gate_incomplete": len(incomplete), "zero_interception": len(zero),
              "sympy_over_60_minutes": len(long), "matplotlib_cross_interruption_separate": 1}
    assert list(counts.values()) == [2, 8, 3, 2, 1]
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "verification": {
                "counts": verification["counts"], "completed_pairs": verification["completed_pairs"]},
            "counts": counts, "zero_interception": zero, "resolved_gate_incomplete": incomplete,
            "long_elapsed": long, "host_sleep_events": sleep,
            "anomaly_category_memberships": 16, "unique_anomalous_jobs": 15,
            "count_note": "The four main categories contain 15 memberships and 14 unique jobs; SymPy-18211 gated belongs to two categories. The separately listed Matplotlib baseline adds one unique job (15 table rows).",
            "sources_sha256": sources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / ".pytest_tmp/verified20/formal20_logicstar")
    parser.add_argument("--tasks", type=Path, default=ROOT / ".pytest_tmp/verified20/verified20_logicstar.json")
    parser.add_argument("--sleep-events", type=Path, default=ROOT / "benchmarks/swebench/results/verified20_host_sleep_events.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root, args.tasks, args.sleep_events)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
