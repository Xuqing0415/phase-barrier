"""Evidence-checked recovery; preserve Agent code, sample, old attempts and outcomes."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from scripts import run_swebench_batch_container as batch
from scripts import scale100_runtime as runtime
from scripts.scale100_classify_existing import classify, digest, read, safe_label, saved_agent
from scripts.verified20_checks import monitoring_decision, model_call_failed
from scripts.verified20_runtime import atomic_write, save_json
from anti_shortcut.state import _file_lock


def now():
    return datetime.now(timezone.utc).isoformat()


def rows(root):
    with (root / "results.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_result(root, row):
    """Atomically replace the canonical CSV; immutable attempts retain prior rows."""
    row = {key: row.get(key, "") for key in batch.COLUMNS}
    with _file_lock(root / "results_merge.lock"):
        prior = rows(root)
        key = row["instance_id"], row["mode"]
        existing = [r for r in prior if (r["instance_id"], r["mode"]) == key]
        if any(r.get("note") == "graded" for r in existing):
            raise ValueError("refusing to overwrite a graded result")
        prior = [r for r in prior if (r["instance_id"], r["mode"]) != key]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=batch.COLUMNS)
        writer.writeheader()
        writer.writerows(prior + [row])
        atomic_write(root / "results.csv", stream.getvalue())


def snapshot(root, state, reason=None):
    data = rows(root)
    pairs = defaultdict(set)
    for row in data:
        if row.get("note") == "graded": pairs[row["instance_id"]].add(row["mode"])
    events = []
    index = read(root / "evidence_index.json")["jobs"]
    for key, selected in index.items():
        path = root / "agent_runs" / safe_label(selected["agent_label"]) / "stats.json"
        if path.exists():
            stats = read(path)
            if stats["mode"] == "gated" and stats.get("test_preservation_intercepts", 0):
                events.append({"instance_id":stats["instance_id"], "intercepts":stats["test_preservation_intercepts"]})
    result = {"checked_at": now(), "state":state, "halt_reason":reason,
              "completed_jobs":len(data), "planned_jobs":len(index),
              "officially_graded_jobs":sum(r["note"] == "graded" for r in data),
              "completed_pairs":sum(v == {"baseline","gated"} for v in pairs.values()),
              "notes":dict(Counter(r["note"] for r in data)),
              "test_preservation_intercepts":sum(e["intercepts"] for e in events),
              "test_preservation_events":events,
              "zero_interception":[r["instance_id"] for r in data if r["mode"] == "gated" and str(r.get("gate_intercepts")) == "0"],
              "gate_completed_official_unresolved":[r["instance_id"] for r in data if r["mode"] == "gated" and str(r.get("gate_completed")) == "1" and str(r.get("resolved")) == "0"],
              "over_60_minutes":[r["instance_id"]+"/"+r["mode"] for r in data if float(r.get("seconds") or 0) > 3600]}
    save_json(root / "monitor_status.json", result)
    return result


def grade_saved(job, args, tasks):
    """Only invoke the official grader; never create another Agent attempt."""
    root = args.root
    iid, mode = job["instance_id"], job["mode"]
    stats, patch = saved_agent(root, tasks[iid], mode, job["agent_label"], read(root / "prelaunch.json"))
    if digest(patch) != job["patch_sha256"]:
        raise ValueError("saved patch changed after review")
    label = job["grade_label"]
    cmd = [str(args.grade_python), str(args.grade_script), "--instance", iid,
           "--dataset", str(args.dataset), "--patch-file", str(patch),
           "--label", label, "--out-dir", str(args.eval_runs), "--timeout", str(args.grade_timeout)]
    proc, out = batch._run(cmd, args.grade_timeout + 120, args.log_dir / (label + ".grade.log"))
    meta = batch.parse_pb(out)
    note = batch.grade_note_from_pb(meta)
    row = {key:stats.get(key, "") for key in batch.COLUMNS}
    row.update(instance_id=iid, repo=tasks[iid]["repo"], mode=mode,
               resolved=meta.get("PB_RESOLVED", ""), note=note)
    if proc.returncode not in (0,2) or note != "graded" or row["resolved"] not in ("0","1"):
        row.update(resolved="", note="grade_infra_failure")
    return row


def job_args(root, source, attempt):
    return SimpleNamespace(root=root, dataset=source / ".pytest_tmp/scale100_preparation/host_dataset.json",
        agent_script=source / "benchmarks/swebench/run_agent.py", repo_mount=source,
        agent_dataset=root / "_agent_dataset.json", agent_runs=root / "agent_runs",
        grade_script=source / "benchmarks/swebench/grade.py",
        grade_python=source / ".pytest_tmp/sweb_venv/Scripts/python.exe",
        eval_runs=root / "eval_runs", log_dir=root / "logs",
        label_suffix=runtime.SUFFIX+"_"+attempt, isolate_agent_runs=True,
        exclude_preexisting_build_copies=True, gate_python="/opt/miniconda3/bin/python",
        testbed_python="/opt/miniconda3/envs/testbed/bin/python", max_turns=60,
        exec_timeout=300, agent_timeout=5400, grade_timeout=1800,
        dns=["223.5.5.5","1.1.1.1"], test_preservation="strict")


def run_job(job, args, tasks):
    if job["action"] == "regrade_only":
        return grade_saved(job, args, tasks)
    directory = args.agent_runs / safe_label(job["agent_label"])
    if directory.exists():
        raise ValueError("new attempt directory already exists")
    return batch.run_one(job, args, tasks)


def simulated_job(job, args, tasks):
    """Synthetic evidence is confined to a marked, two-instance simulation root."""
    if not (args.root / "SIMULATION_ONLY.json").exists() or len(tasks) != 2:
        raise ValueError("synthetic executor requires a marked two-instance fixture")
    if job["action"] == "regrade_only": raise ValueError("simulation job must generate evidence")
    label, grade = job["agent_label"], job["grade_label"]
    directory = args.agent_runs / label
    directory.mkdir(parents=True,exist_ok=False)
    patch = "diff --git a/demo.py b/demo.py\n--- a/demo.py\n+++ b/demo.py\n@@ -1 +1 @@\n-old\n+new\n"
    stats = {"instance_id":job["instance_id"],"mode":job["mode"],"label":label,
             "turns":1,"seconds":1.0,"diff_chars":len(patch),"gate_intercepts":0,
             "gate_completed":0,"gate_final_stage":0,"test_preservation":"strict",
             "test_preservation_intercepts":0,"started_at_epoch":1,"finished_at_epoch":2,
             "model":"SIMULATION_NO_MODEL"}
    (directory/"model_patch.diff").write_text(patch,encoding="utf-8")
    save_json(directory/"stats.json",stats)
    save_json(directory/"initial_artifacts.json",{"passed":True,"files":[],"source_files_modified":False,"excluded_directory":None})
    if job["mode"] == "gated":
        save_json(directory/"state.json",{"current_stage":0})
        (directory/"trace.jsonl").write_text(json.dumps({"blocked":False,"event":"tool_call","stage_before":0,"stage_after":0,"tool":"read_file","args":{}})+"\n",encoding="utf-8")
    save_json(args.eval_runs/"reports"/(grade+".json"),{job["instance_id"]:{"patch_successfully_applied":True,"resolved":False,"simulation":True}})
    save_json(args.eval_runs/"predictions"/(grade+".json"),[{"instance_id":job["instance_id"],"model_patch":patch}])
    with (args.root/"simulated_model_calls.jsonl").open("a",encoding="utf-8") as stream:
        stream.write(json.dumps({"key":job["instance_id"]+"/"+job["mode"]})+"\n")
    return {**{k:stats.get(k,"") for k in batch.COLUMNS},"repo":tasks[job["instance_id"]]["repo"],"resolved":"0","note":"graded"}


def worker(root, source, attempt, *, simulated=False, crash_after=0, pause_after=0):
    folder = root / "attempts" / safe_label(attempt)
    save_json(folder/"worker_pid.json",{"runner_pid":os.getpid(),"registered_at":now()})
    schedule = read(folder / "schedule.json")
    tasks = {t["instance_id"]:t for t in read(source / "benchmarks/swebench/manifests/scale100-verified-2026-11-14.json")["tasks"]}
    args = job_args(root, source, attempt)
    iterator = iter(schedule)
    workers = 1 if simulated else 2
    completed = 0
    executor = simulated_job if simulated else run_job
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running = {}
        for job in list(schedule[:workers]): running[pool.submit(executor, job, args, tasks)] = job
        for _ in range(min(workers,len(schedule))): next(iterator)
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                job = running.pop(future)
                try:
                    row = future.result()
                except Exception as exc:
                    row = {key:job[key] for key in ("instance_id","repo","mode")}
                    row.update(resolved="",note="grade_infra_failure driver_error:"+type(exc).__name__)
                with (folder / "result_history.jsonl").open("a",encoding="utf-8") as stream:
                    stream.write(json.dumps(row)+"\n"); stream.flush(); os.fsync(stream.fileno())
                write_result(root, row)
                completed += 1
                if simulated and completed == pause_after:
                    while True: time.sleep(0.1)  # Wait for the real supervisor to stop this process.
                if simulated and completed == crash_after:
                    raise PermissionError("injected atomic status replacement failure after completed job")
                if monitoring_decision([row], args.log_dir, args.label_suffix, stop_on_unknown_empty=False)["stop"]:
                    return 2
                next_job = next(iterator, None)
                if next_job is not None: running[pool.submit(executor,next_job,args,tasks)] = next_job
    return 0


def configure(source, root):
    runtime.ROOT, runtime.RUN_ROOT = source, root
    runtime.MANIFEST = source / "benchmarks/swebench/manifests/scale100-verified-2026-11-14.json"
    runtime.PREP = source / ".pytest_tmp/scale100_preparation"
    runtime.HOST_DATASET = runtime.PREP / "host_dataset.json"
    runtime.TESTBED_AUDIT = source / "benchmarks/swebench/results/scale100-testbeds-2026-11-14.json"


def check_archive(root, archive):
    proof = read(archive / "archive_verification.json")
    if not proof.get("verified") or Path(proof["source_root"]).resolve() != root:
        raise ValueError("archive is not verified for this run")
    for relative, record in proof["files"].items():
        if digest(archive / relative) != record["sha256"] or digest(root / relative) != record["sha256"]:
            raise ValueError("archived run or current evidence changed: "+relative)
    for relative, expected in proof["source_snapshot"].items():
        if digest(archive / "_source_snapshot" / relative) != expected:
            raise ValueError("archived source mismatch")
    return {"archive":str(archive), "proof_sha256":digest(archive / "archive_verification.json"),
            "files_verified":len(proof["files"])}


def published_ci():
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=CODE,text=True,encoding="utf-8").strip()
    if subprocess.check_output(["git","status","--porcelain"],cwd=CODE,text=True,encoding="utf-8").strip():
        raise ValueError("controller worktree must be clean")
    remote = subprocess.check_output(["git","ls-remote","origin","refs/heads/main"],cwd=CODE,text=True,encoding="utf-8").split()[0]
    if remote != commit: raise ValueError("controller must be published on main")
    runs = json.loads(subprocess.check_output(["gh","run","list","--commit",commit,
        "--json","name,status,conclusion,databaseId"],cwd=CODE,text=True,encoding="utf-8"))
    for name in ("CI","docs","Security","Plugin CI"):
        found = [r for r in runs if r["name"] == name]
        if len(found) != 1 or found[0]["conclusion"] != "success":
            raise ValueError("published recovery CI is not green: "+name)
    return {"commit":commit, "runs":runs}


def prepare(args):
    root, source = args.run_root.resolve(), args.execution_root.resolve()
    configure(source,root)
    attempt = safe_label(args.attempt_label)
    if not re.fullmatch(r"resume[1-9][0-9]*",attempt): raise ValueError("use an explicit resumeN label")
    if (root / "attempts" / attempt).exists(): raise ValueError("attempt label already exists")
    status = read(root / "status.json")
    if status.get("state") != "halted" or runtime.pid_is_alive(status.get("runner_pid")):
        raise ValueError("run is not durably halted with a dead runner")
    archive = check_archive(root,args.archive.resolve())
    launch = read(root / "launch.json")
    for relative,expected in launch["source_hashes"].items():
        if digest(source / relative) != expected: raise ValueError("frozen execution source changed: "+relative)
    if subprocess.check_output(["git","diff","v1.2.0","--","anti_shortcut"],cwd=source):
        raise ValueError("Agent gate differs from v1.2.0")
    plan = classify(root,runtime.MANIFEST)
    if plan != read(args.resume_plan): raise ValueError("resume plan differs from current evidence")
    # No status or evidence writes occur until all review gates have passed.
    ci = published_ci()
    simulated = read(CODE / "benchmarks/swebench/results/scale100-recovery-validation.json")
    if not simulated.get("passed") or not simulated.get("simulation",{}).get("passed"):
        raise ValueError("crash/resume simulation has not passed")
    preflight = runtime.preflight(probe_testbeds=False, allow_existing=True)
    if preflight["source_hashes"] != launch["source_hashes"]:
        raise ValueError("preflight frozen source set changed")
    folder = root / "attempts" / attempt
    folder.mkdir(parents=True,exist_ok=False)
    save_json(folder / "preflight.json", {"archive":archive,"published_ci":ci,"preflight":preflight})
    save_json(folder / "reviewed_plan.json",plan)
    index = {"jobs":{}}
    schedule = []
    for action in ("skip","regrade_only","rerun","pending"):
        for record in plan[action]:
            job = {**record,"action":action}
            if action != "skip":
                label = batch.short_id(job["instance_id"])+"_"+job["mode"]+runtime.SUFFIX+"_"+attempt
                job["grade_label"] = safe_label(label)
                if action != "regrade_only": job["agent_label"] = label
                for directory in (root/"agent_runs"/label,root/"eval_runs/reports"/(label+".json"),root/"eval_runs/predictions"/(label+".json")):
                    if directory.exists(): raise ValueError("attempt output already exists")
                schedule.append(job)
            index["jobs"][job["instance_id"]+"/"+job["mode"]] = {
                "agent_label":job["agent_label"],"grade_label":job["grade_label"]}
    save_json(folder / "schedule.json",schedule)
    save_json(root / "evidence_index.json",index)
    interruptions = [{"instance_id":r["instance_id"],"mode":r["mode"],"interrupted_agent_label":r["agent_label"],
                      "interrupted_at":read(root/"halt.json")["observed_at"],
                      "reason":"supervisor_permission_error_interrupted_agent", "evidence":{"source":str(args.archive.resolve()/"halt.json"),"agent_directory":r["agent_label"]}}
                     for r in plan["rerun"]]
    save_json(folder / "interrupted_jobs.json",{"jobs":interruptions})
    save_json(root / "supervisor_interruptions.json",{"pairs":[{"instance_id":r["instance_id"],"reason":r["reason"],"evidence":r["evidence"]} for r in interruptions]})
    return folder, plan, ci


def halt(root, runner, reason, runner_pid=None):
    """Stop before writing diagnostics, so a broken status disk cannot orphan work."""
    actual_pid=runner.pid if runner_pid is None else runner_pid
    stop_errors=[]
    try:
        if runtime.pid_is_alive(actual_pid) or runner.poll() is None:
            if sys.platform == "win32":
                proc=subprocess.run(["taskkill","/PID",str(actual_pid),"/T","/F"],capture_output=True,timeout=30)
                if proc.returncode: stop_errors.append("taskkill_exit_"+str(proc.returncode))
            else:
                runner.terminate()
            if runner.poll() is None: runner.wait(timeout=30)
    except Exception as exc:
        stop_errors.append(type(exc).__name__)
    try:
        if (root/"SIMULATION_ONLY.json").exists():
            save_json(root/"halt.json",{"reason":reason,"cleanup_complete":not stop_errors,"cleanup_errors":stop_errors,"observed_at":now(),"simulation":True})
            save_json(root/"status.json",{"state":"halted","halt_reason":reason,"runner_pid":actual_pid})
        else:
            runtime.stop_batch(actual_pid,reason)
    except Exception as exc:
        fallback = root / ("halt_fallback_"+str(time.time_ns())+".json")
        with fallback.open("x",encoding="utf-8") as stream:
            json.dump({"reason":reason,"error_type":type(exc).__name__,"observed_at":now(),"runner_alive_after_stop":runtime.pid_is_alive(actual_pid)},stream)
    try:
        snapshot(root,"halted_supervisor_error" if reason.startswith("supervisor_error") else "halted_review_required",reason)
    except Exception:
        pass  # Durable halt/fallback and caller exit code remain authoritative.


def supervise(args, *, simulated_prepared=None):
    if simulated_prepared is not None:
        if not (args.run_root/"SIMULATION_ONLY.json").exists():
            raise ValueError("prepared simulation requires a marked fixture")
        folder,plan,ci=simulated_prepared
    else:
        folder, plan, ci = prepare(args)
    root, source = args.run_root.resolve(), args.execution_root.resolve()
    env = dict(os.environ,PYTHONUTF8="1",PYTHONIOENCODING="utf-8",PYTHONUNBUFFERED="1",PB_DS_MODEL="deepseek-v4-flash")
    command = [sys.executable,"-u","-X","utf8",str(Path(__file__).resolve()),"worker",
               "--run-root",str(root),"--execution-root",str(source),"--attempt-label",args.attempt_label]
    if simulated_prepared is not None:
        command += ["--simulate-worker","--pause-after","1"]
    with (folder / "runner.log").open("wb") as log:
        launch = {**read(root/"launch.json"),"runner_pid":None,"supervisor_pid":os.getpid(),
                  "resumed_at":now(),"attempt_label":args.attempt_label,"source_root":str(source),
                  "controller_root":str(CODE),"controller_commit":ci["commit"],
                  "controller_hashes":{p.relative_to(CODE).as_posix():digest(p) for p in
                      [CODE/"scripts"/name for name in ("verified20_runtime.py","scale100_runtime.py","scale100_recovery.py","scale100_classify_existing.py","scale100_analysis.py","scale100_observer.py")]},
                  "command":command}
        runner = subprocess.Popen(command,cwd=CODE,env=env,stdout=log,stderr=subprocess.STDOUT)
        runner_pid=runner.pid
        try:
            deadline=time.monotonic()+60
            while not (folder/"worker_pid.json").exists():
                if runner.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError("worker failed before actual PID registration")
                time.sleep(0.1)
            runner_pid=read(folder/"worker_pid.json")["runner_pid"]
            if not isinstance(runner_pid,int) or not runtime.pid_is_alive(runner_pid):
                raise RuntimeError("registered worker PID is not alive")
            launch.update(runner_pid=runner_pid,launcher_pid=runner.pid)
            save_json(folder/"launch.json",launch)
            save_json(root/"current_launch.json",launch)
            save_json(root/"status.json",{"state":"running","runner_pid":runner_pid,"supervisor_pid":os.getpid(),"halt_reason":None,"attempt_label":args.attempt_label,"resumed_at":launch["resumed_at"]})
            next_history = 0
            startup_deadline = time.monotonic()+300
            startup_checked = False
            while runner.poll() is None:
                if simulated_prepared is not None and len(rows(root)) == 1:
                    raise PermissionError("injected permanent monitor replacement failure")
                decision = monitoring_decision(rows(root),root/"logs",runtime.SUFFIX+"_"+args.attempt_label,stop_on_unknown_empty=False)
                model_error = any(model_call_failed(p) for p in (root/"logs").glob("*"+args.attempt_label+".container.log"))
                if decision["stop"] or model_error:
                    halt(root,runner,decision["reason"] or "model_call_error",runner_pid)
                    return 2
                state = snapshot(root,"running")
                if time.monotonic() >= next_history:
                    save_json(folder/"checkpoint.json",state)
                    next_history=time.monotonic()+3600
                if not startup_checked and time.monotonic() >= startup_deadline:
                    if (runtime.frozen_files() != launch["source_hashes"]
                            or read(root/"status.json")["runner_pid"] != runner_pid
                            or not runtime.pid_is_alive(runner_pid)):
                        raise RuntimeError("recovery five-minute startup verification failed")
                    save_json(folder/"startup_verification.json",{"passed":True,"checked_at":now(),
                        "runner_pid":runner_pid,"supervisor_pid":os.getpid(),"source_hashes_match":True,
                        "elapsed_seconds":300,"observed_progress":state})
                    startup_checked=True
                time.sleep(2)
            code = runner.wait()
            if code or len(rows(root)) != plan["total_jobs"]:
                halt(root,runner,"resume_worker_incomplete",runner_pid)
                return 3
            runtime.review_host_interruptions(read(root/"launch.json")["launched_at"])
            exclusions = read(root/"cost_exclusions.json")
            exclusions["pairs"] += read(root/"supervisor_interruptions.json")["pairs"]
            save_json(root/"cost_exclusions.json",exclusions)
            proc = subprocess.run([sys.executable,"-X","utf8",str(CODE/"scripts/scale100_analysis.py"),
                "--manifest",str(runtime.MANIFEST),"--run-root",str(root),"--source-root",str(source),
                "--cost-exclusions",str(root/"cost_exclusions.json")],cwd=CODE,env=env,capture_output=True)
            (folder/"final_verification.log").write_bytes(proc.stdout+proc.stderr)
            state = "verified_complete_pending_report_review" if proc.returncode == 0 else "completion_evidence_review_required"
            save_json(root/"status.json",{"state":state,"runner_pid":runner_pid,"attempt_label":args.attempt_label,"halt_reason":None})
            snapshot(root,state)
            return proc.returncode
        except BaseException as exc:
            try:
                save_json(folder/"supervisor_error.json",{"error_type":type(exc).__name__,"error":str(exc),"observed_at":now()})
            except Exception:
                pass
            halt(root,runner,"supervisor_error:"+type(exc).__name__,runner_pid)
            return 4


def simulate_cycle(output):
    """Use separate worker processes for a four-job crash/resume acceptance test."""
    output.mkdir(parents=True,exist_ok=False)
    root, source = output/"run", output/"source"
    root.mkdir(); source.mkdir()
    tasks = [{"instance_id":"demo__fixture-"+str(i),"repo":"demo/fixture"} for i in (1,2)]
    manifest=source/"benchmarks/swebench/manifests/scale100-verified-2026-11-14.json"
    save_json(manifest,{"tasks":tasks})
    save_json(root/"SIMULATION_ONLY.json",{"synthetic":True,"real_model_calls":0})
    save_json(root/"launch.json",{"source_hashes":{},"launched_at":now()})
    save_json(root/"prelaunch.json",{"manifest_sha256":digest(manifest),"source_hashes":{},"initial_artifacts":{t["instance_id"]:[] for t in tasks}})
    stream=io.StringIO(newline=""); csv.DictWriter(stream,fieldnames=batch.COLUMNS).writeheader()
    atomic_write(root/"results.csv",stream.getvalue())
    index={"jobs":{}}
    schedule=[]
    for task in tasks:
        for mode in ("baseline","gated"):
            label=batch.short_id(task["instance_id"])+"_"+mode+runtime.SUFFIX
            job={**task,"mode":mode,"action":"pending","agent_label":label,"grade_label":label}
            schedule.append(job); index["jobs"][task["instance_id"]+"/"+mode]={"agent_label":label,"grade_label":label}
    save_json(root/"evidence_index.json",index)
    save_json(root/"attempts/resume1/schedule.json",schedule)
    base=[sys.executable,"-X","utf8",str(Path(__file__).resolve()),"worker","--run-root",str(root),"--execution-root",str(source),"--simulate-worker"]
    env=dict(os.environ,PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
    fake_args=SimpleNamespace(run_root=root,execution_root=source,attempt_label="resume1")
    first_exit=supervise(fake_args,simulated_prepared=(root/"attempts/resume1",{"total_jobs":4},{"commit":"SIMULATION_NO_PUBLISH"}))
    if first_exit != 4 or read(root/"status.json")["state"] != "halted":
        raise ValueError("simulated supervisor did not durably halt after injected failure")
    plan=classify(root,manifest)
    if plan["counts"] != {"skip":1,"regrade_only":0,"rerun":0,"pending":3}:
        raise ValueError("simulation did not preserve its first result")
    first_files={str(p):digest(p) for p in (root/"agent_runs"/schedule[0]["agent_label"]).rglob("*") if p.is_file()}
    resumed=[]
    for job in plan["pending"]:
        label=job["agent_label"]+"_resume2"
        resumed.append({**job,"action":"pending","agent_label":label,"grade_label":label})
        index["jobs"][job["instance_id"]+"/"+job["mode"]]={"agent_label":label,"grade_label":label}
    save_json(root/"evidence_index.json",index)
    save_json(root/"attempts/resume2/schedule.json",resumed)
    second=subprocess.run(base+["--attempt-label","resume2"],capture_output=True,env=env)
    if second.returncode: raise ValueError("simulated resume failed: "+second.stderr.decode("utf-8",errors="replace"))
    final=classify(root,manifest)
    calls=[json.loads(line)["key"] for line in (root/"simulated_model_calls.jsonl").read_text().splitlines()]
    from scripts.scale100_analysis import verify
    proof, _, _ = verify(manifest,root)
    passed=(proof["passed"] and final["counts"]["skip"] == 4 and len(calls) == len(set(calls)) == 4
            and all(digest(p) == h for p,h in first_files.items()))
    result={"passed":passed,"synthetic":True,"instances":2,"jobs":4,"first_exit":first_exit,
            "resume_exit":second.returncode,"skipped_after_crash":1,"unique_simulated_model_calls":len(set(calls)),
            "real_model_calls":0,"supervisor_failure_path_exercised":True,"completion_evidence_passed":proof["passed"],"first_attempt_unchanged":all(digest(p)==h for p,h in first_files.items())}
    save_json(output/"simulation_result.json",result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("resume","worker","simulate"))
    parser.add_argument("--run-root",type=Path,required=True)
    parser.add_argument("--execution-root",type=Path)
    parser.add_argument("--attempt-label",default="resume1")
    parser.add_argument("--archive",type=Path)
    parser.add_argument("--resume-plan",type=Path)
    parser.add_argument("--simulate-worker",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--crash-after",type=int,default=0,help=argparse.SUPPRESS)
    parser.add_argument("--pause-after",type=int,default=0,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.action == "simulate":
        result=simulate_cycle(args.run_root.resolve()); print(json.dumps(result)); return 0 if result["passed"] else 1
    if not args.execution_root: parser.error("execution-root is required")
    if args.action == "worker":
        try:
            code=worker(args.run_root.resolve(),args.execution_root.resolve(),safe_label(args.attempt_label),simulated=args.simulate_worker,crash_after=args.crash_after,pause_after=args.pause_after)
            return code
        except PermissionError as exc:
            if not args.simulate_worker: raise
            save_json(args.run_root/"status.json",{"state":"halted","halt_reason":"supervisor_error:PermissionError"})
            save_json(args.run_root/"halt.json",{"reason":"supervisor_error:PermissionError","cleanup_complete":True,"simulation":True})
            return 4
    if not args.archive or not args.resume_plan: parser.error("resume requires --archive and --resume-plan")
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
