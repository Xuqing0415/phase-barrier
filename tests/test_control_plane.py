"""Control-plane invariants: privileges, approvals, ownership and full gate cycles."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.control.diff import ReviewRequired
from anti_shortcut.control.events import tail_events
from anti_shortcut.control.scheduler import Scheduler, validate_schedule
from anti_shortcut.proxy import GateProxy, WriteDenied
from anti_shortcut.proxy_client import GateClient, GateDenied
from anti_shortcut.sidecar import GateSidecar, make_server
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC


@pytest.fixture
def enrolled(tmp_path):
    skill = AntiShortcutSkill(tmp_path, config={"state_hmac_key": "control-test-key"})
    token = skill.control.permissions.bootstrap()
    with skill.control.permissions.session(token):
        yield skill, token
    skill.close()


def issue(skill, role="worker", actor="agent"):
    return skill.control.permissions.issue(role, actor)


def complete_cycle(skill):
    proxy = GateProxy(skill)
    if skill.current_stage == 0:
        assert skill.advance_stage(1)["success"]
    proxy.write_file("spec.md", SPEC)
    assert skill.advance_stage(2)["success"]
    proxy.write_file("test_fib.py", GOOD_TESTS)
    assert skill.advance_stage(3)["success"]
    proxy.write_file("fib.py", GOOD_IMPL)
    assert skill.advance_stage(4)["success"]
    skill.state.mark_test_run({"passed": True, "total": 3, "exit_code": 0, "at_epoch": time.time()})
    assert skill.advance_stage(5)["success"]
    assert skill.current_stage == 6


def test_legacy_tools_work_but_new_admin_operations_need_enrollment(tmp_path):
    skill = AntiShortcutSkill(tmp_path)
    GateProxy(skill).write_file("spec.md", SPEC)
    with pytest.raises(PermissionError):
        skill.control.goal_create("Goal", ["assert result"], ["first"])
    assert skill.current_stage == 1


def test_observer_cannot_write_execute_advance_or_report(enrolled):
    skill, _ = enrolled
    observer = issue(skill, "observer")
    with skill.control.permissions.session(observer["token"]):
        assert skill.control.snapshot()["session"]["role"] == "observer"
        for operation in [lambda: skill.check_write_permission("spec.md"),
                          lambda: skill.check_exec_permission("echo ok"),
                          lambda: skill.advance_stage(2),
                          lambda: skill.control.permissions.require("record_test_run")]:
            with pytest.raises(PermissionError, match="权限拒绝"):
                operation()
    log = (skill.gate_dir / "audit.log").read_text(encoding="utf-8")
    assert "permission.denied" in log
    assert not (skill.workspace / "spec.md").exists()


def test_sessions_are_server_bound_revocable_and_tokens_not_stored(enrolled):
    skill, _ = enrolled
    worker = issue(skill)
    assert worker["token"].encode() not in (skill.gate_dir / "tasks.db").read_bytes()
    with skill.control.permissions.session(worker["token"]):
        with pytest.raises(PermissionError):
            issue(skill, "admin", "human")
        with pytest.raises(PermissionError):
            skill.control.permissions.update({"version": 1})
    skill.control.permissions.revoke(worker["session_id"])
    with skill.control.permissions.session(worker["token"]), pytest.raises(PermissionError):
        skill.control.permissions.require("read_state")
    with pytest.raises(ValueError):
        skill.control.permissions.bootstrap()


def test_policy_deleted_or_malformed_fails_closed(enrolled):
    skill, _ = enrolled
    skill.control.permissions.path.unlink()
    with pytest.raises(PermissionError, match="策略"):
        skill.check_write_permission("spec.md")


@pytest.mark.parametrize("policy", [{}, {"version": 1, "roles": {"worker": ["policy_manage"]}},
                                    {"version": 1, "review_writes": "*.py"},
                                    {"version": 1, "write_paths": {"worker": "src/*"}},
                                    {"version": 1, "roles": None},
                                    {"version": 1, "roles": {"worker": [["write_file"]]}},
                                    {"version": 1, "unexpected": "allow"}])
def test_invalid_policies_rejected(enrolled, policy):
    with pytest.raises(ValueError):
        enrolled[0].control.permissions.update(policy)


def test_role_scope_restricts_paths_and_never_skips_stage(enrolled):
    skill, _ = enrolled
    worker = issue(skill)
    skill.control.permissions.update({"version": 1, "write_paths": {"worker": ["docs/*", "fib.py"]}})
    with skill.control.permissions.session(worker["token"]):
        with pytest.raises(WriteDenied, match="范围"):
            GateProxy(skill).write_file("spec.md", SPEC)
        with pytest.raises(WriteDenied, match="测试用例"):
            GateProxy(skill).write_file("fib.py", GOOD_IMPL)
        assert GateProxy(skill).write_file("docs/readme.md", "hello")["ok"]


def test_diff_preview_is_before_write_and_approval_bound_to_content(enrolled):
    skill, admin = enrolled
    worker = issue(skill)
    skill.control.permissions.update({"version": 1, "review_writes": ["spec.md"]})
    proxy = GateProxy(skill)
    with skill.control.permissions.session(worker["token"]):
        with pytest.raises(WriteDenied, match="人工复核"):
            proxy.write_file("spec.md", SPEC)
        assert not (skill.workspace / "spec.md").exists()
    preview = skill.control.snapshot()["diff"]
    assert "+## 需求分析" in preview["diff"]
    assert preview["status"] == "pending"
    assert json.loads(skill.control.diffs.path.read_text(encoding="utf-8"))["review_id"] == preview["review_id"]
    skill.control.diffs.decide(preview["review_id"], True, "checked spec")
    with skill.control.permissions.session(worker["token"]):
        with pytest.raises(WriteDenied):
            proxy.write_file("spec.md", SPEC + "\nchanged")
        assert proxy.write_file("spec.md", SPEC)["ok"]
    assert (skill.workspace / "spec.md").read_text(encoding="utf-8") == SPEC
    with pytest.raises(ValueError):
        skill.control.diffs.decide(preview["review_id"], True, "reuse")
    with skill.control.permissions.session(worker["token"]):
        with pytest.raises(PermissionError, match="write_file"):
            skill.check_exec_permission("echo altered > spec.md")


def test_diff_approval_does_not_transfer_to_other_session_or_old_file(enrolled):
    skill, _ = enrolled
    first, second = issue(skill), issue(skill)
    skill.control.permissions.update({"version": 1, "review_writes": ["spec.md"]})
    with skill.control.permissions.session(first["token"]), pytest.raises(WriteDenied):
        GateProxy(skill).write_file("spec.md", SPEC)
    preview = skill.control.snapshot()["diff"]
    skill.control.diffs.decide(preview["review_id"], True, "approved first session")
    with skill.control.permissions.session(second["token"]), pytest.raises(WriteDenied):
        GateProxy(skill).write_file("spec.md", SPEC)
    (skill.workspace / "spec.md").write_text("external change", encoding="utf-8")
    with skill.control.permissions.session(first["token"]), pytest.raises(WriteDenied):
        GateProxy(skill).write_file("spec.md", SPEC)


def test_diff_rejection_and_nonhuman_review_are_enforced(enrolled):
    skill, _ = enrolled
    maintainer = issue(skill, "maintainer")
    skill.control.permissions.update({"version": 1, "review_writes": ["spec.md"]})
    with pytest.raises(ReviewRequired):
        skill.wrap_write_file(lambda *_: None)("spec.md", SPEC)
    preview = skill.control.snapshot()["diff"]
    with skill.control.permissions.session(maintainer["token"]), pytest.raises(PermissionError):
        skill.control.diffs.decide(preview["review_id"], True, "agent cannot review")
    skill.control.diffs.decide(preview["review_id"], False, "needs revision")
    with pytest.raises(WriteDenied, match="rejected"):
        GateProxy(skill).write_file("spec.md", SPEC)


def test_binary_and_failed_writes_are_not_marked_written(tmp_path):
    skill = AntiShortcutSkill(tmp_path)
    def fail(*args):
        raise OSError("write failed")
    with pytest.raises(OSError):
        skill.wrap_write_file(fail)("blob.bin", b"\0binary")
    preview = skill.control.snapshot()["diff"]
    assert preview["binary"] and preview["status"] == "preview"


def test_task_capture_claim_return_main_and_persistence(enrolled):
    skill, _ = enrolled
    worker = issue(skill)
    stage = skill.state.snapshot()
    with skill.control.permissions.session(worker["token"]):
        task = skill.control.task_capture("fix typo", notes={"source": "spec"})
    skill.control.task_transition(task["task_id"], "queued")
    with skill.control.permissions.session(worker["token"]):
        skill.control.task_transition(task["task_id"], "active")
        with pytest.raises(ValueError, match="return-to-main"):
            skill.control.task_transition(task["task_id"], "done")
        assert skill.control.task_transition(task["task_id"], "done", True)["status"] == "done"
    assert skill.state.snapshot() == stage
    other = AntiShortcutSkill(skill.workspace, config=skill.config)
    with other.control.permissions.session(worker["token"]):
        assert other.control.tasks()[0]["status"] == "done"
    other.close()


def test_concurrent_task_claim_has_one_winner_and_human_ownership(enrolled):
    skill, admin = enrolled
    task = skill.control.task_capture("a task")
    skill.control.task_transition(task["task_id"], "queued")
    workers = [issue(skill), issue(skill)]
    def claim(session):
        with skill.control.permissions.session(session["token"]):
            try:
                skill.control.task_transition(task["task_id"], "active")
                return True
            except (ValueError, PermissionError):
                return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(claim, workers)) == 1
    human_task = skill.control.task_capture("human work")
    skill.control.task_transition(human_task["task_id"], "queued")
    assert skill.control.task_transition(human_task["task_id"], "active")["human_owned"]
    with skill.control.permissions.session(workers[0]["token"]), pytest.raises(PermissionError):
        skill.control.task_transition(human_task["task_id"], "done", True)


def test_goal_full_cycles_pause_resume_cross_session_and_evidence(enrolled):
    skill, admin = enrolled
    goal = skill.control.goal_create("two cycles", ["fib returns correct values"], ["first", "second"])
    assert skill.current_stage == 0
    assert skill.state.snapshot()["goal_id"] == goal["goal_id"]
    with pytest.raises(ValueError):
        skill.control.goal_action(goal["goal_id"], "next")
    skill.control.goal_action(goal["goal_id"], "pause")
    with pytest.raises(PermissionError):
        skill.advance_stage(1)
    with pytest.raises(WriteDenied):
        GateProxy(skill).write_file("spec.md", SPEC)
    other = AntiShortcutSkill(skill.workspace, config=skill.config, session_token=admin)
    other.control.goal_action(goal["goal_id"], "resume")
    complete_cycle(other)
    next_goal = other.control.goal_action(goal["goal_id"], "next")
    assert other.current_stage == 0 and next_goal["current_milestone"] == 1
    assert next_goal["evidence"][0]["manifest"]["spec.md"]["sha256"]
    assert not other.state.get_evidence("last_test_run")
    assert other.state.snapshot()["goal_cycles"][0]["current_stage"] == 6
    complete_cycle(other)
    assert other.control.goal_action(goal["goal_id"], "next")["status"] == "completed"
    other.close()


def test_goal_requires_dod_and_cannot_reset_unfinished_work(enrolled):
    skill, _ = enrolled
    with pytest.raises(ValueError):
        skill.control.goal_create("goal", [], ["first"])
    with pytest.raises(ValueError):
        skill.control.goal_create("goal", ["check"], [""])
    GateProxy(skill).write_file("spec.md", SPEC)
    assert skill.advance_stage(2)["success"]
    with pytest.raises(ValueError, match="阶段"):
        skill.control.goal_create("cannot reset", ["check"], ["first"])


def test_break_glass_exact_once_expiry_and_stage_checks(enrolled):
    skill, _ = enrolled
    observer = issue(skill, "observer")
    token = skill.control.break_glass_issue("docs/note.md", observer["session_id"], "human maintenance")["token"]
    with skill.control.permissions.session(observer["token"]):
        with pytest.raises(WriteDenied):
            GateProxy(skill).write_file("docs/other.md", "x", break_glass=token)
        assert GateProxy(skill).write_file("docs/note.md", "x", break_glass=token)["ok"]
        with pytest.raises(WriteDenied):
            GateProxy(skill).write_file("docs/note.md", "x", break_glass=token)
    source = skill.control.break_glass_issue("fib.py", observer["session_id"], "test stage checks")["token"]
    with skill.control.permissions.session(observer["token"]), pytest.raises(WriteDenied, match="测试用例"):
        GateProxy(skill).write_file("fib.py", GOOD_IMPL, break_glass=source)
    with pytest.raises(PermissionError):
        skill.control.break_glass_issue(".agent_gate/state.json", observer["session_id"], "no")


def test_scheduler_fixed_handlers_leases_and_audit(enrolled):
    skill, _ = enrolled
    task = skill.control.task_capture("old task")
    with skill.control.store.transaction() as db:
        task["updated_at"] = time.time() - 40 * 86400
        skill.control.store.put(db, "task", task["task_id"], task)
    config = {"version": 1, "jobs": {name: {} for name in [
        "evidence_integrity_check", "btw_stale_cleanup", "residual_expiry_check", "goal_health_report"]}}
    scheduler = Scheduler(skill, config)
    results = scheduler.run_pending(now=1000)
    assert len(results) == 4
    assert scheduler.run_pending(now=1001) == []
    assert Scheduler(skill, config).run_pending(now=1001) == []
    assert skill.control.tasks()[0]["stale"]
    assert skill.current_stage == 1
    assert (skill.gate_dir / "reports/goal-health.json").exists()
    assert "scheduler.completed" in (skill.gate_dir / "audit.log").read_text(encoding="utf-8")
    scheduler.start()
    scheduler.stop()
    assert not scheduler._thread.is_alive()


@pytest.mark.parametrize("jobs", [{"shell": {}}, {"red_team_fast": {"command": "rm"}},
                                  {"goal_health_report": {"interval_seconds": float("nan")}},
                                  {"goal_health_report": {"interval_seconds": 0}}])
def test_scheduler_rejects_arbitrary_actions_and_invalid_timing(jobs):
    with pytest.raises(ValueError):
        validate_schedule({"version": 1, "jobs": jobs})


def test_event_tail_partial_lines_cursor_and_rotation(tmp_path):
    path = tmp_path / "events.log"
    assert tail_events(path) == {"cursor": 0, "events": []}
    line = json.dumps({"event": "proxy_write_ok", "timestamp": "now"}) + "\n"
    path.write_text(line + '{"event":', encoding="utf-8")
    first = tail_events(path)
    assert first["events"][0]["type"] == "tool.complete"
    assert tail_events(path, first["cursor"])["events"] == []
    path.write_text('{"event":"stage_advanced"}\n', encoding="utf-8")
    assert tail_events(path, 9999)["events"][0]["type"] == "stage.changed"


def test_http_authentication_and_control_dispatch(tmp_path):
    sidecar = GateSidecar(tmp_path)
    admin = sidecar.skill.control.permissions.bootstrap()
    server = make_server(sidecar, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        root = GateClient(url, token=admin)
        observer = root.control("session_issue", role="observer")["result"]
        viewer = GateClient(url, token=observer["token"])
        assert viewer.control_snapshot()["session"]["role"] == "observer"
        with pytest.raises(GateDenied):
            GateClient(url).state()
        for call in [lambda: viewer.write_file("spec.md", SPEC), lambda: viewer.advance(2),
                     lambda: viewer.record_test_run(0, "3 passed"),
                     lambda: viewer.control("policy_update", policy={"version": 1})]:
            with pytest.raises(GateDenied):
                call()
        assert root.control("task_capture", title="human task")["result"]["status"] == "captured"
        assert viewer.events()["events"]
        from anti_shortcut.proxy_client import GateClientError
        for operation in ["bootstrap", "start_scheduler", "_data"]:
            with pytest.raises(GateClientError):
                root.control(operation)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        sidecar.close()


def test_cli_provision_and_task_lifecycle(tmp_path, monkeypatch, capsys):
    from anti_shortcut.__main__ import main
    common = ["--workspace", str(tmp_path)]
    assert main(["permissions-init", *common]) == 0
    admin = json.loads(capsys.readouterr().out)["token"]
    monkeypatch.setenv("PHASE_BARRIER_SESSION_TOKEN", admin)
    assert main(["write", "--path", "spec.md", "--content", SPEC, *common]) == 0
    capsys.readouterr()
    assert main(["task", "capture", "--title", "typo", *common]) == 0
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    for action in ["queue", "claim", "complete"]:
        assert main(["task", action, "--task-id", task_id, "--return-to-main", *common]) == 0
        capsys.readouterr()
    assert main(["goal", "create", "--title", "goal", "--done", "assert result", "--milestone", "first", *common]) == 0


def test_enrolled_skill_does_not_accept_observer_user_request_mutation(enrolled):
    skill, _ = enrolled
    observer = issue(skill, "observer")
    before = skill.state.snapshot()
    with pytest.raises(PermissionError):
        AntiShortcutSkill(skill.workspace, config=skill.config,
                          session_token=observer["token"], user_request="overwrite")
    skill.state.reload()
    assert skill.state.snapshot() == before
    with skill.control.permissions.session(observer["token"]):
        tools = skill.install({})
        assert "goal_create" in tools and "task_capture" in tools
        with pytest.raises(PermissionError):
            tools["goal_create"]("goal", ["check"], ["first"])


def test_tui_headless_displays_server_snapshot(enrolled):
    pytest.importorskip("textual")
    from anti_shortcut.control.tui import GateApp
    skill, _ = enrolled
    snapshot = skill.control.snapshot()
    class Client:
        def events(self, cursor):
            return {"cursor": 1, "events": []}
        def control_snapshot(self):
            return snapshot
    async def check():
        app = GateApp(Client())
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            assert app.data["state"]["current_stage"] == 1
            assert not app.query_one("#approve").disabled
            await pilot.press("s")
    asyncio.run(check())


def test_policy_rate_limits_and_session_expiry(enrolled):
    skill, _ = enrolled
    worker = issue(skill)
    skill.control.permissions.update({"version": 1, "rate_limits": {"write_file": 1}})
    with skill.control.permissions.session(worker["token"]):
        GateProxy(skill).write_file("spec.md", SPEC)
        with pytest.raises(WriteDenied, match="速率"):
            GateProxy(skill).write_file("notes.md", "later")
    with skill.control.store.transaction() as db:
        for row in db.execute("SELECT id FROM records WHERE kind='rate'").fetchall():
            skill.control.store.put(db, "rate", row[0], {"start": 0, "count": 1})
    with skill.control.permissions.session(worker["token"]):
        assert GateProxy(skill).write_file("notes.md", "later")["ok"]
    from anti_shortcut.control.permissions import Permissions
    for role, actor, ttl in [("unknown", "agent", 10), ("worker", "other", 10),
                             ("admin", "agent", 10), ("worker", "agent", 0)]:
        with pytest.raises(ValueError):
            skill.control.permissions.issue(role, actor, ttl)
    with pytest.raises(ValueError):
        Permissions.validate({"version": 1, "rate_limits": {"write_file": False}})
    with skill.control.permissions.session(None), pytest.raises(PermissionError):
        skill.control.permissions.identity()
    with pytest.raises(ValueError):
        skill.control.permissions.revoke("unknown session")


def test_goal_interrupted_binding_resume_and_abandon(enrolled, monkeypatch):
    skill, _ = enrolled
    original = skill.state.start_goal_cycle
    def crash(*args, **kwargs):
        raise OSError("process interrupted before state binding")
    monkeypatch.setattr(skill.state, "start_goal_cycle", crash)
    with pytest.raises(OSError):
        skill.control.goal_create("recoverable", ["check"], ["first"])
    goal = skill.control.goals()[0]
    with pytest.raises(ValueError):
        skill.control.goal_create("cannot replace", ["check"], ["other"])
    monkeypatch.setattr(skill.state, "start_goal_cycle", original)
    skill.control.goal_action(goal["goal_id"], "resume")
    assert skill.current_stage == 0
    for action in ["unknown"]:
        with pytest.raises(ValueError):
            skill.control.goal_action(goal["goal_id"], action)
    with pytest.raises(ValueError):
        skill.control.goal_action("unknown", "pause")
    assert skill.control.goal_action(goal["goal_id"], "abandon")["status"] == "abandoned"
    with pytest.raises(PermissionError):
        skill.check_exec_permission("echo cannot execute abandoned goal")


def test_goal_next_rejects_tampered_delivery(enrolled):
    skill, _ = enrolled
    goal = skill.control.goal_create("integrity", ["tests"], ["first", "second"])
    complete_cycle(skill)
    (skill.workspace / "spec.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="证据"):
        skill.control.goal_action(goal["goal_id"], "next")
    assert skill.current_stage == 6


def test_goal_cycle_compare_and_swap_and_no_reset_of_active_state(enrolled):
    from anti_shortcut.state import StateChangedError
    skill, _ = enrolled
    old = skill.state.snapshot()
    skill.state.set_evidence("concurrent_change", True)
    with pytest.raises(StateChangedError):
        skill.state.start_goal_cycle("id", 0, "first", expected=old, allow_initial=True)
    with pytest.raises(ValueError):
        skill.state.start_goal_cycle("id", 0, "first", expected=skill.state.snapshot())


def test_human_delivery_review_rejection_overrides_approval(enrolled):
    from anti_shortcut.defense._common import write_evidence, read_json, evidence_path
    skill, _ = enrolled
    write_evidence(skill.workspace, skill.config, "human_review_request.json", {"request_id": "review-1"})
    assert skill.control.review_decide("review-1", True, "passed")["approved"]
    assert not skill.control.review_decide("review-1", False, "new issue")["approved"]
    path = evidence_path(skill.workspace, skill.config, skill.config.defense.human_review.approval_file)
    entries = read_json(path)["approvals"]
    assert len(entries) == 1 and entries[0]["approved"] is False
    with pytest.raises(ValueError):
        skill.control.review_decide("stale-review", True, "no")
    with pytest.raises(ValueError):
        skill.control.review_decide("review-1", "yes", "no")


def test_scheduler_expiry_red_team_failures_and_reload_config(enrolled, monkeypatch):
    import subprocess
    from anti_shortcut.control import scheduler as module
    skill, _ = enrolled
    ledger = skill.workspace / "benchmarks/red_team/known_residual.yaml"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("entries:\n- technique: old\n  expires_at: 2000-01-01\n", encoding="utf-8")
    scheduler = Scheduler(skill, {"version": 1, "jobs": {}})
    result = scheduler.execute("residual_expiry_check", {})
    assert not result["ok"] and result["expired"][0]["expires_at"] == "2000-01-01"
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 1, stdout="escaped", stderr=""))
    assert scheduler.execute("red_team_fast", {})["exit_code"] == 1
    assert scheduler.execute("red_team_fast", {})["output"] == "escaped"
    with pytest.raises(ValueError):
        scheduler.execute("arbitrary_shell", {})
    result = skill.control.schedule_update({"version": 1, "jobs": {"goal_health_report": {}}})
    assert result["restart_required"]
    assert (skill.gate_dir / "schedule.yaml").exists()
    maintainer = issue(skill, "maintainer", "agent")
    with skill.control.permissions.session(maintainer["token"]), pytest.raises(PermissionError):
        skill.control.schedule_update({"version": 1, "jobs": {}})
    for invalid in [{}, {"version": 1, "jobs": []}]:
        with pytest.raises(ValueError):
            validate_schedule(invalid)
    scheduler = Scheduler(skill, {"version": 1, "jobs": {"goal_health_report": {}}})
    monkeypatch.setattr(scheduler, "execute", lambda *a: (_ for _ in ()).throw(ValueError("broken")))
    assert not scheduler.run_pending(now=1000)[0]["ok"]


def test_control_dispatch_invalid_arguments_are_bad_requests(enrolled):
    control = enrolled[0].control
    for operation, data in [(None, {}), ([], {}), ("task_capture", []),
                             ("task_capture", {"invalid": "field"}), ("bootstrap", {})]:
        with pytest.raises(ValueError):
            control.dispatch(operation, data)
    for title, type, notes, goal in [("", "btw", None, None), ("x", "other", None, None),
                                    ("x", "btw", [], None), ("x", "btw", None, "missing")]:
        with pytest.raises(ValueError):
            control.task_capture(title, type, goal, notes)
    with pytest.raises(ValueError):
        control.task_transition("missing", "active")


def test_grpc_authentication_uses_metadata_and_denies_observer_writes(tmp_path):
    grpc = pytest.importorskip("grpc")
    from anti_shortcut.grpc_service import create_grpc_server
    from anti_shortcut.proto import sidecar_pb2 as pb, sidecar_pb2_grpc as rpc
    sidecar = GateSidecar(tmp_path)
    token = sidecar.skill.control.permissions.bootstrap()
    with sidecar.skill.control.permissions.session(token):
        viewer = sidecar.skill.control.permissions.issue("observer")
    # Allocate a loopback port before starting the real RPC service.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = create_grpc_server(sidecar, host="127.0.0.1", port=port)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = rpc.PhaseBarrierStub(channel)
            with pytest.raises(grpc.RpcError) as denied:
                stub.GetState(pb.GetStateRequest(), timeout=5)
            assert denied.value.code() == grpc.StatusCode.PERMISSION_DENIED
            metadata = [("authorization", "Bearer " + viewer["token"])]
            assert stub.GetState(pb.GetStateRequest(), metadata=metadata, timeout=5).current_stage == 1
            with pytest.raises(grpc.RpcError) as denied:
                stub.Advance(pb.AdvanceRequest(new_stage=2), metadata=metadata, timeout=5)
            assert denied.value.code() == grpc.StatusCode.PERMISSION_DENIED
    finally:
        server.stop(0).wait()
        sidecar.close()


def test_sidecar_owns_scheduler_lifecycle(tmp_path):
    sidecar = GateSidecar(tmp_path)
    assert sidecar.scheduler is None
    sidecar.start_scheduler()
    assert sidecar.scheduler is None
    (sidecar.skill.gate_dir / "schedule.yaml").write_text(
        "version: 1\njobs:\n  goal_health_report: {}\n", encoding="utf-8")
    sidecar.start_scheduler()
    first = sidecar.scheduler
    sidecar.start_scheduler()
    assert sidecar.scheduler is first
    sidecar.close()
    assert not first._thread.is_alive()


def test_event_mapping_and_corrupt_records(tmp_path):
    from anti_shortcut.control.events import event_type
    assert event_type("diff.preview") == "write.preview"
    assert event_type("diff.review_requested") == "human_input_required"
    assert event_type("stage_advance_rejected") == "tool.blocked"
    with pytest.raises(ValueError):
        tail_events(tmp_path / "missing", -1)
    path = tmp_path / "events"
    path.write_text('invalid\n[]\n{"event":"custom.event"}\n', encoding="utf-8")
    assert len(tail_events(path)["events"]) == 1


def test_tui_diff_render_actions_errors_and_observer_restriction(enrolled):
    pytest.importorskip("textual")
    from anti_shortcut.control.tui import GateApp
    from anti_shortcut.proxy_client import GateClientError
    from textual.widgets import Button, Input, Select
    skill, _ = enrolled
    GateProxy(skill).write_file("spec.md", SPEC)
    task = skill.control.task_capture("human task")
    snapshot = skill.control.snapshot()
    calls = []
    class Client:
        fail = False
        def events(self, cursor):
            if self.fail:
                raise GateClientError("disconnected")
            return {"cursor": 1, "events": [{"timestamp": "now", "type": "tool.complete", "data": {}}]}
        def control_snapshot(self):
            return snapshot
        def control(self, operation, **data):
            calls.append((operation, data))
            if data.get("reason") == "fail":
                raise GateClientError("forbidden")
    async def check():
        client = Client()
        app = GateApp(client)
        async with app.run_test(size=(160, 60)) as pilot:
            await pilot.pause()
            assert len(app.query("DiffView")) == 1
            await pilot.press("s")
            app.query_one("#review-id", Input).value = "review"
            app.query_one("#reason", Input).value = "checked"
            await app.on_button_pressed(Button.Pressed(app.query_one("#approve", Button)))
            assert calls[-1][0] == "diff_decide" and calls[-1][1]["approved"]
            app.query_one("#review-kind", Select).value = "review_decide"
            app.query_one("#reason", Input).value = "fail"
            await app.on_button_pressed(Button.Pressed(app.query_one("#reject", Button)))
            app.query_one("#task-id", Input).value = task["task_id"]
            await app.on_button_pressed(Button.Pressed(app.query_one("#claim", Button)))
            assert calls[-1][1]["status"] == "active"
            snapshot["session"] = {"role": "observer", "session_id": "observer", "actor": "agent"}
            snapshot["diff"]["binary"] = True
            snapshot["diff"]["status"] = "pending"
            await app.render_snapshot()
            assert app.query_one("#approve", Button).disabled
            client.fail = True
            await app.refresh_control()
    asyncio.run(check())
