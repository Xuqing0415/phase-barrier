"""Classify saved Scale-100 evidence without calling a model or changing artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verified20_checks import audit_persisted_run
from scripts.verified20_runtime import save_json

SUFFIX = "_scale100_20261114"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    for attempt in range(8):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, PermissionError):
            if attempt == 7: raise
            time.sleep(0.1)


def safe_label(label):
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", label) or label in (".", ".."):
        raise ValueError("unsafe evidence label")
    return label


def evidence_labels(root, iid, mode):
    default = iid.replace("/", "_").replace("__", "_") + "_" + mode + SUFFIX
    path = Path(root) / "evidence_index.json"
    if not path.exists():
        return default, default
    index = read(path)
    key = iid + "/" + mode
    record = index["jobs"][key]
    return safe_label(record["agent_label"]), safe_label(record["grade_label"])


def saved_agent(root, task, mode, label, prelaunch):
    directory = root / "agent_runs" / safe_label(label)
    patch = directory / "model_patch.diff"
    stats = read(directory / "stats.json")
    text = patch.read_text(encoding="utf-8")
    if (stats.get("instance_id") != task["instance_id"] or stats.get("mode") != mode
            or stats.get("label") != label or stats.get("diff_chars") != len(text) or not text):
        raise ValueError("saved Agent identity or patch mismatch: " + label)
    if any(float(stats[k]) < 0 for k in ("turns", "seconds", "gate_intercepts", "gate_completed")):
        raise ValueError("invalid saved Agent metrics: " + label)
    if mode == "gated" and (stats.get("test_preservation") != "strict"
                            or not audit_persisted_run(directory)["passed"]):
        raise ValueError("invalid strict gated evidence: " + label)
    if "initial_artifacts" in prelaunch:
        initial = read(directory / "initial_artifacts.json")
        expected = prelaunch["initial_artifacts"][task["instance_id"]]
        if (initial.get("passed") is not True or initial.get("source_files_modified") is not False
                or initial.get("files") != expected
                or initial.get("excluded_directory") != ("build/" if expected else None)):
            raise ValueError("initial artifact mismatch: " + label)
    return stats, patch


def classify(root: Path, manifest: Path) -> dict:
    root = root.resolve()
    tasks = read(manifest)["tasks"]
    expected = {(t["instance_id"], mode) for t in tasks for mode in ("baseline", "gated")}
    if len(expected) != 2 * len(tasks):
        raise ValueError("duplicate manifest keys")
    result = root / "results.csv"
    with result.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    by_key = {}
    for row in rows:
        key = row["instance_id"], row["mode"]
        if key not in expected or key in by_key:
            raise ValueError("unexpected or duplicate result key")
        by_key[key] = row
    prelaunch = read(root / "prelaunch.json")
    if prelaunch.get("manifest_sha256") != digest(manifest):
        raise ValueError("manifest differs from launch")
    plan = {k: [] for k in ("skip", "regrade_only", "rerun", "pending")}
    evidence = {"results.csv": digest(result), "prelaunch.json": digest(root / "prelaunch.json")}
    halt = read(root / "halt.json") if (root / "halt.json").exists() else {}
    for task in tasks:
        iid = task["instance_id"]
        for mode in ("baseline", "gated"):
            agent, grade = evidence_labels(root, iid, mode)
            record = {"instance_id": iid, "mode": mode, "repo": task["repo"],
                      "agent_label": agent, "grade_label": grade}
            directory = root / "agent_runs" / agent
            patch = directory / "model_patch.diff"
            stats_path = directory / "stats.json"
            row = by_key.get((iid, mode))
            if patch.is_file() and patch.stat().st_size and stats_path.is_file():
                stats, patch = saved_agent(root, task, mode, agent, prelaunch)
                report = root / "eval_runs/reports" / (grade + ".json")
                prediction = root / "eval_runs/predictions" / (grade + ".json")
                record["patch_sha256"] = digest(patch)
                if row and row.get("note") == "graded" and report.exists() and prediction.exists():
                    official = read(report)[iid]
                    predictions = read(prediction)
                    if (not official.get("patch_successfully_applied") or official.get("infra_failure")
                            or int(bool(official["resolved"])) != int(row["resolved"])
                            or len(predictions) != 1 or predictions[0]["instance_id"] != iid
                            or predictions[0]["model_patch"] != patch.read_text(encoding="utf-8")):
                        raise ValueError("official evidence mismatch: " + agent)
                    for key in ("turns", "seconds", "diff_chars", "gate_intercepts", "gate_completed"):
                        if float(stats[key]) != float(row[key]):
                            raise ValueError("CSV/stats mismatch: " + agent + "/" + key)
                    category = "skip"
                else:
                    category = "regrade_only"
                for p in directory.rglob("*"):
                    if p.is_file(): evidence[p.relative_to(root).as_posix()] = digest(p)
                for p in (report, prediction):
                    if p.is_file(): evidence[p.relative_to(root).as_posix()] = digest(p)
            elif stats_path.exists():
                # An Agent that finished with unknown empty output is an outcome,
                # not authorization to sample the model again.
                raise ValueError("finished empty/invalid patch requires separate review: " + agent)
            elif directory.exists() or row:
                if not halt.get("cleanup_complete") or not halt.get("reason", "").startswith("supervisor_error:"):
                    raise ValueError("incomplete run lacks reviewed interruption evidence: " + agent)
                category = "rerun"
                record["reason"] = "supervisor_interrupted_before_saved_stats"
                for p in directory.rglob("*"):
                    if p.is_file(): evidence[p.relative_to(root).as_posix()] = digest(p)
            else:
                category = "pending"
            plan[category].append(record)
    done = defaultdict(set)
    for record in plan["skip"]: done[record["instance_id"]].add(record["mode"])
    plan.update(schema=1, root=str(root), manifest_sha256=digest(manifest),
                input_hashes=evidence, counts={k: len(v) for k,v in plan.items()},
                completed_pairs=sum(v == {"baseline", "gated"} for v in done.values()),
                total_jobs=len(expected), total_remaining=len(expected)-len(plan["skip"]),
                total_model_calls_needed=len(plan["rerun"])+len(plan["pending"]),
                total_regrade_only=len(plan["regrade_only"]), passed=True)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = classify(args.root, args.manifest)
    save_json(args.output, plan)
    print(json.dumps({k:plan[k] for k in ("counts","completed_pairs","total_remaining", "total_model_calls_needed", "total_regrade_only")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
