"""Preflight, launch and supervise the fixed Scale-100 paired experiment."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.swebench.run_status import pid_is_alive
from scripts.run_swebench_batch_container import AGENT_DATASET_FIELDS, write_agent_dataset
from scripts.verified20_checks import monitoring_decision, model_call_failed
from scripts.verified20_runtime import save_json, stop_verified_batch

PREREG_COMMIT = "44fb2a8ceab4738e761fb9e7e7498428b66fb73b"
MANIFEST = ROOT / "benchmarks/swebench/manifests/scale100-verified-2026-11-14.json"
HOST_DATASET = ROOT / ".pytest_tmp/scale100_preparation/host_dataset.json"
PREP = ROOT / ".pytest_tmp/scale100_preparation"
RUN_ROOT = ROOT / ".pytest_tmp/scale100_20261114"
SUFFIX = "_scale100_20261114"
TESTBED_AUDIT = ROOT / "benchmarks/swebench/results/scale100-testbeds-2026-11-14.json"


def read_json(path: Path) -> dict:
    for attempt in range(8):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if attempt == 7:
                raise
            time.sleep(0.1)
    raise AssertionError("unreachable")


def git_bytes(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, timeout=30).stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_files() -> dict[str, str]:
    paths = sorted((ROOT / "anti_shortcut").rglob("*.py"))
    paths += [ROOT / relative for relative in (
        "benchmarks/swebench/run_agent.py", "benchmarks/swebench/grade.py",
        "scripts/run_swebench_batch_container.py", "scripts/verified20_checks.py",
        "scripts/verified20_runtime.py", "scripts/scale100_runtime.py",
        "scripts/scale100_analysis.py",
        "benchmarks/swebench/results/scale100-testbeds-2026-11-14.json",
        "benchmarks/swebench/results/scale100-images-2026-11-14.json",
    )]
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths}


def inspect_testbed(task: dict) -> dict:
    probe = '''import json,subprocess,sys
def git(*args):
    return subprocess.check_output(['git','-c','safe.directory=/testbed','-C','/testbed',*args],text=True,encoding='utf-8').strip()
base=sys.argv[1]
version=subprocess.check_output(['/opt/miniconda3/envs/testbed/bin/python','--version'],text=True,encoding='utf-8',stderr=subprocess.STDOUT).strip()
print(json.dumps({'head':git('rev-parse','HEAD'),'head_tree':git('rev-parse','HEAD^{tree}'),
 'base_tree':git('rev-parse',base+'^{tree}'),'status':git('status','--porcelain'),
 'changed_files':git('diff','--name-only',base,'HEAD').splitlines(),
 'diff':git('diff',base,'HEAD'),'head_subject':git('log','-1','--format=%s'),
 'testbed_python':version,'gate_python':sys.version.split()[0]}))
'''
    proc = subprocess.run([
        "docker", "run", "--rm", "--dns", "223.5.5.5", "--dns", "1.1.1.1",
        "--entrypoint", "/opt/miniconda3/bin/python", task["image"], "-c", probe, task["base_commit"],
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    actual = json.loads(proc.stdout) if proc.returncode == 0 else {}
    matched = (proc.returncode == 0 and actual.get("head_tree") == actual.get("base_tree")
               and not actual.get("status"))
    return {"instance_id": task["instance_id"], "image": task["image"],
            "base_commit": task["base_commit"], "passed": matched,
            "observed_head": actual.get("head"),
            "testbed_python": actual.get("testbed_python"), "actual": actual,
            "returncode": proc.returncode}


def verify_testbed_records(checks: list[dict], audit: dict) -> dict:
    """Match live observations to individually reviewed, frozen starting trees."""
    approved = {row["instance_id"]: row for row in audit["records"]}
    if not audit.get("passed") or len(approved) != 100 or len(checks) != 100:
        raise ValueError("100 individually approved testbed records are required")
    if len({row["instance_id"] for row in checks}) != 100:
        raise ValueError("duplicate live testbed record")
    for row in checks:
        fixed = approved.get(row["instance_id"])
        if not fixed or not fixed.get("approved") or row["returncode"] != 0:
            raise ValueError("unapproved testbed: " + row["instance_id"])
        if any(row[key] != fixed[key] for key in ("image", "base_commit", "actual")):
            raise ValueError("testbed differs from reviewed starting state: " + row["instance_id"])
    return {"passed": True, "count": 100, "audit_sha256": sha256(TESTBED_AUDIT), "checks": checks}


def preflight(*, probe_testbeds: bool) -> dict:
    if RUN_ROOT.exists():
        raise ValueError("fresh run root must not already exist; resume requires evidence review")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("required model credential is absent")
    if os.environ.get("PB_DS_MODEL") not in (None, "", "deepseek-v4-flash"):
        raise ValueError("model environment conflicts with the preregistered request name")
    if os.environ.get("PB_DS_API_URL"):
        raise ValueError("unexpected model endpoint override")
    for relative in (
        "benchmarks/swebench/manifests/scale100-verified-2026-11-14.json",
        "benchmarks/swebench/manifests/exclude-historical-49.json",
        "benchmarks/swebench/manifests/scale100-verified-2026-11-14.md",
        "docs/benchmarks/scale100-statistical-protocol.md",
    ):
        committed = git_bytes("show", PREREG_COMMIT + ":" + relative).replace(b"\r\n", b"\n")
        local = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
        if local != committed:
            raise ValueError("preregistered file changed: " + relative)
    origin_head = git_bytes("ls-remote", "origin", "refs/heads/main").decode().split()[0]
    if subprocess.run(["git", "merge-base", "--is-ancestor", PREREG_COMMIT, origin_head], cwd=ROOT).returncode:
        raise ValueError("preregistration is not in the published main history")
    manifest = read_json(MANIFEST)
    exclusion = read_json(ROOT / manifest["exclusion_manifest"])
    tasks = manifest["tasks"]
    ids = {task["instance_id"] for task in tasks}
    if len(tasks) != 100 or len(ids) != 100 or ids & set(exclusion["instance_ids"]):
        raise ValueError("sample count, uniqueness or exclusion validation failed")
    if len({task["repo"] for task in tasks}) != 10:
        raise ValueError("expected ten selected repositories")
    full = read_json(HOST_DATASET)
    if not isinstance(full, list) or {row["instance_id"] for row in full} != ids or len(full) != 100:
        raise ValueError("host grading dataset does not match the fixed sample")
    full_by_id = {row["instance_id"]: row for row in full}
    for task in tasks:
        if any(full_by_id[task["instance_id"]].get(key) != value for key, value in task.items()):
            raise ValueError("public/host dataset mismatch: " + task["instance_id"])
    grade_python = ROOT / ".pytest_tmp/sweb_venv/Scripts/python.exe"
    if not grade_python.is_file():
        raise ValueError("official grading Python is absent")
    subprocess.run([str(grade_python), "-c", "import docker, swebench"], check=True, timeout=30)
    provenance = read_json(ROOT / "benchmarks/swebench/results/scale100-images-2026-11-14.json")
    if provenance.get("manifest_sha256") != sha256(MANIFEST) or provenance.get("ready") != 100:
        raise ValueError("image provenance does not match the frozen sample")
    readiness = read_json(PREP / "image_readiness.json")
    if readiness.get("ready") != 100 or readiness.get("missing_count") or readiness.get("errors"):
        raise ValueError("not all 100 fixed images are ready")
    imported = {row["instance_id"]: row for row in provenance["images"]}
    live_images = json.loads(subprocess.run(
        ["docker", "image", "inspect", *[task["image"] for task in tasks]],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60).stdout)
    if len(live_images) != 100:
        raise ValueError("live Docker image inspection did not return 100 images")
    for task, image in zip(tasks, live_images):
        fixed = imported[task["instance_id"]]
        if image["Id"] != fixed["docker_image_id"] or image["RootFS"]["Layers"] != fixed["rootfs_diff_ids"]:
            raise ValueError("live Docker image changed: " + task["instance_id"])
    for row in readiness["ready_images"]:
        fixed = imported[row["instance_id"]]
        if row["rootfs_layers"] != fixed["rootfs_diff_ids"] or row["image_id"] != fixed["docker_image_id"]:
            raise ValueError("ready image differs from imported content: " + row["instance_id"])
    safe_dataset = write_agent_dataset(HOST_DATASET, ids, PREP / "agent_dataset_validation.json")
    safe_rows = read_json(safe_dataset)
    if any(set(row) != set(AGENT_DATASET_FIELDS) for row in safe_rows):
        raise ValueError("Agent dataset allowlist mismatch")
    disk = {drive: round(shutil.disk_usage(drive + ":/").free / 1024**3, 2) for drive in ("C", "D")}
    if min(disk.values()) < 10:
        raise ValueError("less than 10 GiB free on a required drive")
    if probe_testbeds:
        checks = []
        probe_log = PREP / ("testbed_live_checks_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".jsonl")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row in pool.map(inspect_testbed, tasks):
                checks.append(row)
                with probe_log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
                if len(checks) % 10 == 0:
                    print(json.dumps({"testbeds_checked": len(checks),
                                      "base_tree_clean": sum(item["passed"] for item in checks)}), flush=True)
        save_json(PREP / "testbed_readiness.json", verify_testbed_records(checks, read_json(TESTBED_AUDIT)))
    testbeds = read_json(PREP / "testbed_readiness.json")
    if not testbeds.get("passed") or testbeds.get("count") != 100:
        raise ValueError("100 fixed testbeds have not been verified")
    verify_testbed_records(testbeds["checks"], read_json(TESTBED_AUDIT))
    if testbeds.get("audit_sha256") != sha256(TESTBED_AUDIT):
        raise ValueError("testbed audit changed since preflight")
    dns_code = "import socket; print(len(socket.getaddrinfo('api.deepseek.com',443)))"
    dns_probe = subprocess.run([
        "docker", "run", "--rm", "--dns", "223.5.5.5", "--dns", "1.1.1.1",
        "--entrypoint", "/opt/miniconda3/bin/python", tasks[0]["image"], "-c", dns_code,
    ], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if dns_probe.returncode or not dns_probe.stdout.strip().isdigit() or int(dns_probe.stdout.strip()) < 1:
        raise ValueError("explicit container DNS probe failed")
    result = {"passed": True, "checked_at": datetime.now(timezone.utc).isoformat(),
              "source_commit": git_bytes("rev-parse", "HEAD").decode().strip(),
              "preregistration_commit": PREREG_COMMIT, "origin_main": origin_head,
              "manifest_sha256": sha256(MANIFEST), "host_dataset_sha256": sha256(HOST_DATASET),
              "image_provenance_sha256": sha256(ROOT / "benchmarks/swebench/results/scale100-images-2026-11-14.json"),
              "testbed_audit_sha256": sha256(TESTBED_AUDIT),
              "initial_artifacts": {row["instance_id"]: row["review"].get("artifact_evidence", {}).get("files", [])
                                    for row in read_json(TESTBED_AUDIT)["records"]},
              "source_hashes": frozen_files(), "disk_free_gib": disk,
              "agent_rows": len(safe_rows), "testbed_count": 100,
              "api_key_present": True, "model_request": "deepseek-v4-flash",
              "test_preservation": "strict", "dns": ["223.5.5.5", "1.1.1.1"]}
    save_json(PREP / "prelaunch.json", result)
    return result


def result_rows() -> list[dict]:
    path = RUN_ROOT / "results.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return [row for row in csv.DictReader(stream) if row.get("note") is not None]


def snapshot(state: str) -> dict:
    rows = result_rows()
    pairs: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("note") == "graded":
            pairs[row["instance_id"]].add(row["mode"])
    preservation = []
    for path in (RUN_ROOT / "agent_runs").glob("*/stats.json"):
        stats = read_json(path)
        if stats.get("mode") == "gated" and int(stats.get("test_preservation_intercepts", 0)):
            preservation.append({"instance_id": stats["instance_id"],
                                 "intercepts": stats["test_preservation_intercepts"]})
    data = {"checked_at": datetime.now(timezone.utc).isoformat(), "state": state,
            "completed_jobs": len(rows), "planned_jobs": 200,
            "officially_graded_jobs": sum(row["note"] == "graded" for row in rows),
            "completed_pairs": sum(modes == {"baseline", "gated"} for modes in pairs.values()),
            "notes": dict(Counter(row["note"] for row in rows)),
            "test_preservation_intercepts": sum(row["intercepts"] for row in preservation),
            "test_preservation_events": preservation,
            "zero_interception": [row["instance_id"] for row in rows
                                  if row["mode"] == "gated" and str(row.get("gate_intercepts")) == "0"],
            "gate_completed_official_unresolved": [row["instance_id"] for row in rows
                if row["mode"] == "gated" and row.get("gate_completed") == "1" and row.get("resolved") == "0"],
            "over_60_minutes": [row["instance_id"] + "/" + row["mode"] for row in rows
                                if row.get("seconds") and float(row["seconds"]) > 3600]}
    save_json(RUN_ROOT / "monitor_status.json", data)
    return data


def stop_batch(pid: int, reason: str) -> dict:
    result = stop_verified_batch(RUN_ROOT, pid, reason, pid_probe=pid_is_alive)
    # The official grader copies files into its container, so it has no batch
    # bind mount. Identify only exact names derived from this batch's persisted
    # predictions and the grader's content-hash naming rule.
    prefixes = set()
    for path in (RUN_ROOT / "eval_runs/predictions").glob("*.json"):
        predictions = read_json(path)
        if isinstance(predictions, list) and len(predictions) == 1:
            prediction = predictions[0]
            digest = hashlib.sha256(prediction["model_patch"].encode("utf-8")).hexdigest()[:12]
            prefixes.add(f"sweb.eval.{prediction['instance_id'].lower()}.{path.stem}-{digest}")
    if prefixes:
        try:
            proc = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Names}}"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
            for line in proc.stdout.splitlines():
                identifier, name = line.split(maxsplit=1)
                if any(name == prefix or re.fullmatch(re.escape(prefix) + r"\.\d+", name)
                       for prefix in prefixes):
                    subprocess.run(["docker", "rm", "-f", identifier], capture_output=True,
                                   timeout=30, check=True)
                    result["removed_containers"].append(identifier)
        except Exception as exc:
            result["cleanup_errors"].append({"operation": "cleanup_official_grader", "error": type(exc).__name__})
    result["cleanup_complete"] = not result["cleanup_errors"] and not result["runner_alive_after_stop"]
    save_json(RUN_ROOT / "halt.json", result)
    status_path = RUN_ROOT / "status.json"
    status = read_json(status_path)
    status["cleanup_complete"] = result["cleanup_complete"]
    save_json(status_path, status)
    return result


def review_host_interruptions(launched_at: str) -> dict:
    if sys.platform != "win32":
        raise ValueError("this experiment requires the Windows host power-event audit")
    start_epoch = datetime.fromisoformat(launched_at).timestamp()
    powershell = (
        "$ErrorActionPreference='Stop'; "
        f"$pbStart=[DateTimeOffset]::FromUnixTimeSeconds({int(start_epoch)}).LocalDateTime; "
        "$pbErrors=@(); $pbEvents=@(Get-WinEvent -FilterHashtable @{LogName='System'; "
        "ProviderName='Microsoft-Windows-Power-Troubleshooter'; Id=1; StartTime=$pbStart} "
        "-ErrorAction SilentlyContinue -ErrorVariable pbErrors); "
        "$pbQueryErrors=@($pbErrors | Where-Object {$_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*'}); "
        "if($pbQueryErrors.Count -gt 0){throw 'Power-event query failed'}; "
        "$pbRecords=@(foreach($pbEvent in $pbEvents){[xml]$pbXml=$pbEvent.ToXml(); $pbData=@{}; "
        "foreach($pbItem in $pbXml.Event.EventData.Data){$pbData[$pbItem.Name]=[string]$pbItem.'#text'}; "
        "@{record_id=$pbEvent.RecordId; event_time_utc=$pbEvent.TimeCreated.ToUniversalTime().ToString('o'); "
        "sleep_time=$pbData['SleepTime']; wake_time=$pbData['WakeTime']}}); "
        "@{query_passed=$true; events=$pbRecords} | ConvertTo-Json -Depth 6 -Compress"
    )
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", powershell],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode:
        raise ValueError("external Windows power-event audit failed; cost analysis requires review")
    events = json.loads(proc.stdout)
    save_json(RUN_ROOT / "host_power_events.json", events)
    intervals = []
    for event in events.get("events", []):
        if event.get("sleep_time") and event.get("wake_time"):
            intervals.append((datetime.fromisoformat(event["sleep_time"].replace("Z", "+00:00")).timestamp(),
                              datetime.fromisoformat(event["wake_time"].replace("Z", "+00:00")).timestamp(),
                              event["record_id"]))
    affected: dict[str, set[int]] = defaultdict(set)
    for path in (RUN_ROOT / "agent_runs").glob("*/stats.json"):
        stats = read_json(path)
        started, finished = stats.get("started_at_epoch"), stats.get("finished_at_epoch")
        if started is None or finished is None:
            raise ValueError("a run has no recorded timing boundary")
        for sleep_start, sleep_end, record_id in intervals:
            if float(started) < sleep_end and float(finished) > sleep_start:
                affected[stats["instance_id"]].add(record_id)
    exclusions = {"external_audit_passed": True, "pairs": [
        {"instance_id": iid, "reason": "recorded Agent interval intersects host sleep",
         "evidence": {"source": "host_power_events.json", "record_ids": sorted(record_ids)}}
        for iid, record_ids in sorted(affected.items())
    ]}
    save_json(RUN_ROOT / "cost_exclusions.json", exclusions)
    return exclusions


def supervise() -> int:
    prelaunch = preflight(probe_testbeds=False)
    RUN_ROOT.mkdir(exist_ok=False)
    save_json(RUN_ROOT / "prelaunch.json", prelaunch)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               PB_DS_MODEL="deepseek-v4-flash")
    command = [
        sys.executable, "-u", "-X", "utf8", str(ROOT / "scripts/run_swebench_batch_container.py"),
        "--tasks", str(MANIFEST), "--dataset", str(HOST_DATASET),
        "--grade-python", str(ROOT / ".pytest_tmp/sweb_venv/Scripts/python.exe"),
        "--results", str(RUN_ROOT / "results.csv"), "--agent-runs", str(RUN_ROOT / "agent_runs"),
        "--eval-runs", str(RUN_ROOT / "eval_runs"), "--log-dir", str(RUN_ROOT / "logs"),
        "--modes", "baseline,gated", "--test-preservation", "strict", "--max-turns", "60",
        "--concurrency", "2", "--dns", "223.5.5.5", "--dns", "1.1.1.1",
        "--label-suffix", SUFFIX, "--record-unknown-empty-patches", "--isolate-agent-runs",
        "--exclude-preexisting-build-copies",
    ]
    with (RUN_ROOT / "runner.log").open("wb") as log:
        runner = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        runner_pid = runner.pid
        launch = {
            "launched_at": datetime.now(timezone.utc).isoformat(), "runner_pid": None,
            "launcher_pid": runner.pid,
            "supervisor_pid": os.getpid(), "command": command,
            "preregistration_commit": PREREG_COMMIT, "source_hashes": prelaunch["source_hashes"],
        }
        save_json(RUN_ROOT / "launch.json", launch)
        try:
            deadline = time.monotonic() + 60
            while not (RUN_ROOT / "status.json").exists():
                if runner.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("runner failed before PID/status registration")
                time.sleep(0.2)
            status = read_json(RUN_ROOT / "status.json")
            reported_pid = status.get("runner_pid")
            if not isinstance(reported_pid, int) or not pid_is_alive(reported_pid) or runner.poll() is not None:
                raise RuntimeError("registered runner PID is not alive")
            runner_pid = reported_pid
            launch["runner_pid"] = runner_pid
            save_json(RUN_ROOT / "launch.json", launch)
            save_json(RUN_ROOT / "pid_verification.json", {"passed": True, "runner_pid": runner_pid,
                                                          "launcher_pid": runner.pid,
                                                          "supervisor_pid": os.getpid()})
            save_json(RUN_ROOT / "resume_plan.json", {
                "complete": False, "total_jobs": 200,
                "skip": [], "regrade_only": [], "rerun": [],
                "pending": [{"instance_id": task["instance_id"], "mode": mode}
                            for task in read_json(MANIFEST)["tasks"] for mode in ("baseline", "gated")],
            })
            next_snapshot = 0.0
            last_pair_bucket = -1
            while runner.poll() is None:
                rows = result_rows()
                decision = monitoring_decision(rows, RUN_ROOT / "logs", SUFFIX, stop_on_unknown_empty=False)
                live_model_error = any(model_call_failed(path) for path in (RUN_ROOT / "logs").glob("*.container.log"))
                if decision["stop"] or live_model_error:
                    reason = decision["reason"] or "model_call_error"
                    stop_batch(runner_pid, reason)
                    snapshot("halted_review_required")
                    return 2
                snap = snapshot("running")
                if snap["completed_pairs"] // 10 > last_pair_bucket or time.monotonic() >= next_snapshot:
                    with (RUN_ROOT / "monitor_history.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(snap) + "\n")
                    last_pair_bucket = snap["completed_pairs"] // 10
                    next_snapshot = time.monotonic() + 3600
                if time.monotonic() > deadline + 240 and not any((RUN_ROOT / "agent_runs").glob("*/stats.json")):
                    has_trace = any(path.stat().st_size for path in (RUN_ROOT / "agent_runs").glob("*/trace.jsonl"))
                    has_stdout = any(path.stat().st_size for path in (RUN_ROOT / "logs").glob("*.container.log"))
                    if not has_trace and not has_stdout:
                        raise RuntimeError("no Agent output within five minutes")
                time.sleep(2)
            code = runner.wait()
            decision = monitoring_decision(result_rows(), RUN_ROOT / "logs", SUFFIX, stop_on_unknown_empty=False)
            if decision["stop"]:
                stop_batch(runner_pid, decision["reason"])
                snapshot("halted_review_required")
                return 2
            state = "completed_pending_verification" if code == 0 and len(result_rows()) == 200 else "interrupted_review_required"
            if state == "interrupted_review_required":
                stale = read_json(RUN_ROOT / "status.json")
                stale.update(state="stale", reason="runner exited before verified completion", runner_exit_code=code)
                save_json(RUN_ROOT / "status.json", stale)
            snapshot(state)
            if state == "completed_pending_verification":
                review_host_interruptions(launch["launched_at"])
                result = subprocess.run([
                    sys.executable, "-X", "utf8", str(ROOT / "scripts/scale100_analysis.py"),
                    "--manifest", str(MANIFEST), "--run-root", str(RUN_ROOT),
                    "--cost-exclusions", str(RUN_ROOT / "cost_exclusions.json"),
                ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
                (RUN_ROOT / "final_verification.log").write_bytes(result.stdout)
                snapshot("verified_complete_pending_report_review" if result.returncode == 0 else "completion_evidence_review_required")
                return result.returncode
            return 3
        except BaseException as exc:
            save_json(RUN_ROOT / "supervisor_error.json", {"error_type": type(exc).__name__,
                      "error": str(exc), "observed_at": datetime.now(timezone.utc).isoformat()})
            stop_batch(runner_pid, "supervisor_error:" + type(exc).__name__)
            snapshot("halted_supervisor_error")
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "run"])
    args = parser.parse_args()
    PREP.mkdir(exist_ok=True)
    if args.action == "preflight":
        result = preflight(probe_testbeds=True)
        print(json.dumps({key: result[key] for key in ("passed", "testbed_count", "agent_rows", "disk_free_gib")}))
        return 0
    return supervise()


if __name__ == "__main__":
    raise SystemExit(main())
