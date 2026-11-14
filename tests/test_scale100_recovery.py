"""Recover infrastructure failures without resampling completed model outcomes."""
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verified20_runtime as durable
from scripts import scale100_recovery as recovery
from scripts import scale100_classify_existing as evidence
from scripts import scale100_analysis as analysis
from scripts import scale100_observer as observer


def test_atomic_replace_retries_twice_then_succeeds(tmp_path, monkeypatch):
    original = durable.os.replace
    calls=[]
    def replace(src,dst):
        calls.append(1)
        if len(calls) < 3: raise PermissionError("busy")
        return original(src,dst)
    monkeypatch.setattr(durable.os,"replace",replace)
    monkeypatch.setattr(durable.time,"sleep",lambda _:None)
    durable.save_json(tmp_path/"status.json",{"ok":True})
    assert len(calls)==3 and evidence.read(tmp_path/"status.json")=={"ok":True}


def test_atomic_replace_exhaustion_preserves_original_and_tmp(tmp_path,monkeypatch):
    path=tmp_path/"status.json"
    path.write_text('{"old":true}',encoding="utf-8")
    def fail(*_): raise PermissionError("busy")
    monkeypatch.setattr(durable.os,"replace",fail)
    delays=[]
    monkeypatch.setattr(durable.time,"sleep",delays.append)
    with pytest.raises(PermissionError): durable.save_json(path,{"new":True})
    assert evidence.read(path)=={"old":True}
    assert evidence.read(path.with_name(path.name+".tmp"))=={"new":True}
    assert delays==[0.1,0.2,0.4,0.8]


def test_orphan_tmp_is_replaced_only_by_deliberate_write(tmp_path):
    path=tmp_path/"status.json"
    path.with_name(path.name+".tmp").write_text("orphan",encoding="utf-8")
    durable.save_json(path,{"new":True})
    assert evidence.read(path)=={"new":True}
    assert not path.with_name(path.name+".tmp").exists()


def test_two_threads_write_100_times_each_without_partial_json(tmp_path):
    path=tmp_path/"status.json"
    def writer(number):
        for i in range(100): durable.save_json(path,{"writer":number,"seq":i})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(writer,(1,2)))
    assert evidence.read(path) in ({"writer":1,"seq":99},{"writer":2,"seq":99})


def test_two_processes_share_the_same_atomic_write_lock(tmp_path):
    path=tmp_path/"status.json"
    script="from pathlib import Path; from scripts.verified20_runtime import save_json; import sys; " \
           "[save_json(Path(sys.argv[1]), {'writer':sys.argv[2], 'seq':i}) for i in range(15)]"
    jobs=[subprocess.Popen([recovery.sys.executable,"-X","utf8","-c",script,str(path),str(i)],cwd=recovery.CODE) for i in (1,2)]
    assert [p.wait(timeout=30) for p in jobs]==[0,0]
    assert evidence.read(path)["seq"]==14


@pytest.mark.skipif(recovery.sys.platform != "win32",reason="Windows sharing semantics")
def test_real_windows_read_handle_without_delete_sharing_is_retried(tmp_path,monkeypatch):
    import ctypes
    import threading
    from ctypes import wintypes
    path=tmp_path/"status.json"
    path.write_text('{"old":true}',encoding="utf-8")
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None)
    assert handle != ctypes.c_void_p(-1).value
    timer=threading.Timer(0.25,lambda:kernel.CloseHandle(handle))
    original=durable.os.replace
    conflicts=[]
    def replace(src,dst):
        try:return original(src,dst)
        except PermissionError as exc:
            conflicts.append(getattr(exc,"winerror",None));raise
    monkeypatch.setattr(durable.os,"replace",replace)
    timer.start()
    try:durable.save_json(path,{"new":True})
    finally:timer.join(timeout=5)
    assert conflicts and evidence.read(path)=={"new":True}


@pytest.fixture
def simulated(tmp_path):
    output=tmp_path/"acceptance"
    result=recovery.simulate_cycle(output)
    assert result["passed"]
    return output/"run", output/"source/benchmarks/swebench/manifests/scale100-verified-2026-11-14.json",result


def test_two_instance_subprocess_crash_resume_preserves_first_result(simulated):
    root,manifest,result=simulated
    assert result["first_exit"]==4 and result["resume_exit"]==0
    assert result["real_model_calls"]==0
    assert result["unique_simulated_model_calls"]==4
    assert result["first_attempt_unchanged"] and result["completion_evidence_passed"]
    plan=evidence.classify(root,manifest)
    assert plan["counts"]=={"skip":4,"regrade_only":0,"rerun":0,"pending":0}


def test_missing_report_schedules_grading_without_model_rerun(simulated):
    root,manifest,_=simulated
    plan=evidence.classify(root,manifest)
    record=plan["skip"][0]
    (root/"eval_runs/reports"/(record["grade_label"]+".json")).unlink()
    plan=evidence.classify(root,manifest)
    assert plan["counts"]["skip"]==3 and plan["counts"]["regrade_only"]==1
    assert plan["total_model_calls_needed"]==0


def test_corrupted_official_report_is_rejected(simulated):
    root,manifest,_=simulated
    record=evidence.classify(root,manifest)["skip"][0]
    path=root/"eval_runs/reports"/(record["grade_label"]+".json")
    report=evidence.read(path);report[record["instance_id"]]["resolved"]=True
    durable.save_json(path,report)
    with pytest.raises(ValueError,match="official evidence mismatch"):evidence.classify(root,manifest)


def test_graded_row_is_never_overwritten(simulated):
    root,_,_=simulated
    row=recovery.rows(root)[0]
    before=(root/"results.csv").read_bytes()
    with pytest.raises(ValueError,match="overwrite a graded"): recovery.write_result(root,row)
    assert (root/"results.csv").read_bytes()==before


def test_finished_unknown_empty_patch_is_not_automatically_resampled(simulated):
    root,manifest,_=simulated
    record=evidence.classify(root,manifest)["skip"][0]
    (root/"agent_runs"/record["agent_label"]/"model_patch.diff").write_text("",encoding="utf-8")
    with pytest.raises(ValueError,match="separate review"):evidence.classify(root,manifest)


def test_new_attempt_never_deletes_existing_directory(tmp_path):
    args=SimpleNamespace(agent_runs=tmp_path)
    directory=tmp_path/"existing";directory.mkdir();(directory/"old").write_text("evidence")
    with pytest.raises(ValueError,match="already exists"):
        recovery.run_job({"action":"rerun","agent_label":"existing"},args,{})
    assert (directory/"old").read_text()=="evidence"


def test_regrade_uses_saved_patch_and_never_calls_agent(simulated,monkeypatch):
    root,manifest,_=simulated
    record=evidence.classify(root,manifest)["skip"][0]
    args=recovery.job_args(root,manifest.parents[3],"resume3")
    tasks={t["instance_id"]:t for t in evidence.read(manifest)["tasks"]}
    called=[]
    def run(cmd,*a):
        called.append(cmd)
        return SimpleNamespace(returncode=2),"PB_GRADE_NOTE=graded\nPB_RESOLVED=0\n"
    monkeypatch.setattr(recovery.batch,"_run",run)
    monkeypatch.setattr(recovery.batch,"run_one",lambda *a:pytest.fail("Agent must not run"))
    row=recovery.run_job({**record,"action":"regrade_only"},args,tasks)
    assert row["note"]=="graded" and len(called)==1
    assert "--patch-file" in called[0]


def test_permanent_monitor_write_failure_stops_runner_and_keeps_halt(tmp_path,monkeypatch):
    runner=SimpleNamespace(pid=123,poll=lambda:None,terminate=lambda:None,wait=lambda timeout:0)
    monkeypatch.setattr(recovery.sys,"platform","linux")
    def stopped(pid,reason):durable.save_json(tmp_path/"halt.json",{"reason":reason})
    monkeypatch.setattr(recovery.runtime,"stop_batch",stopped)
    def failed(*args):raise PermissionError("monitor busy")
    monkeypatch.setattr(recovery,"snapshot",failed)
    recovery.halt(tmp_path,runner,"supervisor_error:PermissionError")
    assert evidence.read(tmp_path/"halt.json")["reason"]=="supervisor_error:PermissionError"


def test_exited_launcher_does_not_hide_a_live_actual_worker(tmp_path,monkeypatch):
    runner=SimpleNamespace(pid=10,poll=lambda:0)
    monkeypatch.setattr(recovery.sys,"platform","win32")
    monkeypatch.setattr(recovery.runtime,"pid_is_alive",lambda pid:pid==20)
    calls=[]
    def command(argv,**kwargs):calls.append(argv);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(recovery.subprocess,"run",command)
    def stopped(pid,reason):
        assert pid==20
        durable.save_json(tmp_path/"halt.json",{"reason":reason})
    monkeypatch.setattr(recovery.runtime,"stop_batch",stopped)
    monkeypatch.setattr(recovery,"snapshot",lambda *args:None)
    recovery.halt(tmp_path,runner,"supervisor_error:PermissionError",runner_pid=20)
    assert calls==[["taskkill","/PID","20","/T","/F"]]


def test_recovery_observer_uses_current_pids_and_ignores_archived_halt(tmp_path):
    durable.save_json(tmp_path/"current_launch.json",{"runner_pid":10,"supervisor_pid":20,"resumed_at":"2026-11-14T12:00:00+00:00","source_hashes":{}})
    durable.save_json(tmp_path/"status.json",{"runner_pid":10,"state":"running"})
    durable.save_json(tmp_path/"halt.json",{"reason":"supervisor_error:PermissionError","observed_at":"2026-11-14T09:34:56+00:00"})
    durable.save_json(tmp_path/"monitor_status.json",{"state":"running","completed_jobs":23,"completed_pairs":11,"officially_graded_jobs":23,"test_preservation_intercepts":0})
    result=observer.review(tmp_path,pid_probe=lambda _:True)
    assert result["halt_reason"] is None and not result["terminal_observation"]


def test_tampered_recovery_controller_blocks_final_verification(simulated,tmp_path):
    root,manifest,_=simulated
    durable.save_json(root/"current_launch.json",{"controller_root":str(tmp_path),"controller_hashes":{"absent.py":"0"*64}})
    proof,_,_=analysis.verify(manifest,root)
    assert not proof["passed"] and "recovery_controller_changed:absent.py" in proof["errors"]


@pytest.mark.parametrize("label",["../escape","a/b","..","a\\b",""])
def test_untrusted_evidence_labels_fail_closed(label):
    with pytest.raises(ValueError):evidence.safe_label(label)


@pytest.mark.parametrize("changed",[None,"host_dataset_sha256","model_request","dns","source_hashes","agent_dataset"])
def test_resume_rejects_changed_private_inputs_and_agent_dataset(changed):
    original={key:"frozen" for key in ("manifest_sha256","host_dataset_sha256","image_provenance_sha256",
              "testbed_audit_sha256","model_request","test_preservation","dns","source_hashes")}
    current=dict(original)
    safe=[{"instance_id":"a__a-1","problem_statement":"original"}]
    rebuilt=safe
    if changed=="agent_dataset":rebuilt=[{"instance_id":"a__a-1","problem_statement":"changed"}]
    elif changed:current[changed]="changed"
    if changed:
        with pytest.raises(ValueError,match="differs"):
            recovery.verify_resume_inputs(original,current,safe,rebuilt)
    else:recovery.verify_resume_inputs(original,current,safe,rebuilt)
