"""Controlled writes above the gate; no task or Goal can certify stage evidence."""
from __future__ import annotations

import hashlib
import secrets
import time

from ..state import _file_lock
from .diff import DiffCache
from .permissions import Permissions
from .storage import Store


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value.strip()


class ControlPlane:
    def __init__(self, skill, token=None):
        self.skill = skill
        self.store = Store(skill.gate_dir / "tasks.db")
        self.permissions = Permissions(skill.gate_dir, self.store, skill.logger, token)
        self.diffs = DiffCache(skill, self.store, self.permissions)

    def audit(self, event, identity, **fields):
        self.skill.logger.info(event, session_id=identity["session_id"],
                               role=identity["role"], **fields)

    def assert_running(self):
        context = self.skill.state.snapshot()
        goal_id = context.get("goal_id")
        if goal_id:
            with self.store.transaction() as db:
                goal = self.store.get(db, "goal", goal_id)
            if not goal or goal["status"] != "active":
                raise PermissionError("Goal 已暂停或终止，不能写入、执行命令或推进阶段")
            if context["milestone_index"] != goal["current_milestone"]:
                raise PermissionError("Goal 阶段循环待恢复，请运行 goal resume")

    def tasks(self):
        self.permissions.require("read_tasks")
        with self.store.transaction() as db:
            return self.store.all(db, "task")

    def task_capture(self, title, type="btw", parent_goal_id=None, notes=None):
        identity = self.permissions.require("task_capture")
        title = nonempty(title, "title")
        if type not in {"main", "btw", "blocker"} or (notes is not None and not isinstance(notes, dict)):
            raise ValueError("invalid task type or notes mapping")
        row = {"task_id": secrets.token_hex(12), "type": type, "title": title,
               "status": "captured", "parent_goal_id": parent_goal_id,
               "created_by": identity["actor"], "created_stage": self.skill.current_stage,
               "notes": notes or {}, "claimed_by": None, "human_owned": False,
               "created_at": time.time(), "updated_at": time.time(), "stale": False}
        with self.store.transaction() as db:
            if parent_goal_id and not self.store.get(db, "goal", parent_goal_id):
                raise ValueError("unknown parent goal")
            self.store.put(db, "task", row["task_id"], row)
        self.audit("task.captured", identity, task_id=row["task_id"], type=type)
        return row

    def task_transition(self, task_id, status, return_to_main=False):
        op = {"active": "task_claim", "done": "task_complete"}.get(status, "task_manage")
        identity = self.permissions.require(op)
        transitions = {"captured": {"queued", "dropped"}, "queued": {"active", "dropped"},
                       "active": {"done", "dropped"}, "done": set(), "dropped": set()}
        with self.store.transaction() as db:
            row = self.store.get(db, "task", task_id)
            if not row or status not in transitions[row["status"]]:
                raise ValueError("invalid task transition")
            if row["claimed_by"] and row["claimed_by"] != identity["session_id"]:
                raise PermissionError("任务已由其他会话认领")
            if status == "done" and row["type"] == "btw" and return_to_main is not True:
                raise ValueError("btw completion requires --return-to-main")
            if status == "active":
                row.update(claimed_by=identity["session_id"], human_owned=identity["actor"] == "human")
            row.update(status=status, updated_at=time.time(), stale=False)
            self.store.put(db, "task", task_id, row)
        self.audit("task." + status, identity, task_id=task_id,
                   return_to_main=return_to_main is True)
        return row

    def goals(self):
        self.permissions.require("read_goals")
        with self.store.transaction() as db:
            return self.store.all(db, "goal")

    def goal_create(self, title, definition_of_done, milestones):
        identity = self.permissions.require("goal_manage")
        title = nonempty(title, "title")
        for value, field in [(definition_of_done, "definition_of_done"), (milestones, "milestones")]:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{field} must be a nonempty list")
            for item in value:
                nonempty(item, field)
        with _file_lock(self.skill.gate_dir / "control-operation.lock"):
            self.skill.state.reload()
            snapshot = self.skill.state.snapshot()
            if snapshot.get("goal_id"):
                with self.store.transaction() as db:
                    current = self.store.get(db, "goal", snapshot["goal_id"])
                if not current or current["status"] not in {"completed", "abandoned"}:
                    raise ValueError("已有未结束的 Goal")
            if not self.skill.is_complete and (self.skill.current_stage != 1
                                               or self.skill.state.completed_stages != [0]):
                raise ValueError("只能在首个阶段开始前或已交付后创建 Goal")
            row = {"goal_id": secrets.token_hex(12), "title": title,
                   "definition_of_done": definition_of_done, "milestones": milestones,
                   "status": "active", "current_milestone": 0,
                   "session_ids": [identity["session_id"]], "evidence": [],
                   "created_at": time.time()}
            # Persist intent before changing core state; resume repairs an interrupted bind.
            with self.store.transaction() as db:
                if any(g["status"] in {"active", "paused"} for g in self.store.all(db, "goal")):
                    raise ValueError("已有未结束的 Goal")
                self.store.put(db, "goal", row["goal_id"], row)
            self.skill.state.start_goal_cycle(row["goal_id"], 0, milestones[0],
                                              expected=snapshot, allow_initial=True)
        self.audit("goal.created", identity, goal_id=row["goal_id"])
        return row

    def goal_action(self, goal_id, action):
        identity = self.permissions.require("goal_manage")
        if action not in {"pause", "resume", "next", "abandon"}:
            raise ValueError("unknown goal action")
        with _file_lock(self.skill.gate_dir / "control-operation.lock"):
            self.skill.state.reload()
            snapshot = self.skill.state.snapshot()
            with self.store.transaction() as db:
                row = self.store.get(db, "goal", goal_id)
                if not row or row["status"] not in {"active", "paused"}:
                    raise ValueError("Goal is not active or paused")
                if snapshot.get("goal_id") not in {None, goal_id}:
                    raise ValueError("another Goal owns the gate")
                if action == "pause":
                    row["status"] = "paused"
                elif action == "abandon":
                    row["status"] = "abandoned"
                elif action == "resume":
                    row["status"] = "active"
                else:
                    if row["status"] != "active" or snapshot.get("goal_id") != goal_id:
                        raise ValueError("Goal must be bound and active")
                    if snapshot.get("milestone_index") != row["current_milestone"]:
                        raise ValueError("先 resume 恢复待绑定的阶段循环")
                    if not self.skill.is_complete or not self.skill.state.delivery_clean():
                        raise ValueError("里程碑尚未通过完整阶段门禁或交付后有变更")
                    ok, violations = self.skill.verify_evidence()
                    if not ok:
                        raise ValueError("交付证据不完整：" + "; ".join(violations))
                    row["evidence"].append({"milestone_index": row["current_milestone"],
                                             "state": snapshot,
                                             "manifest": self.skill.evidence_manifest.entries()})
                    row["current_milestone"] += 1
                    if row["current_milestone"] == len(row["milestones"]):
                        row["status"] = "completed"
                if identity["session_id"] not in row["session_ids"]:
                    row["session_ids"].append(identity["session_id"])
                self.store.put(db, "goal", goal_id, row)
            if row["status"] == "active" and (snapshot.get("goal_id") != goal_id or
                    snapshot.get("milestone_index") != row["current_milestone"]):
                self.skill.state.start_goal_cycle(
                    goal_id, row["current_milestone"], row["milestones"][row["current_milestone"]],
                    expected=snapshot, allow_initial=row["current_milestone"] == 0)
        self.audit("goal." + action, identity, goal_id=goal_id, status=row["status"])
        return row

    def review_decide(self, request_id, approved, reason):
        from ..defense.human_review import decide_request

        identity = self.permissions.require_human("review_decide")
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean")
        nonempty(reason, "reason")
        from ..defense._common import read_json, evidence_path
        request = read_json(evidence_path(self.skill.workspace, self.skill.config,
                                         "human_review_request.json"))
        if not request or request.get("request_id") != request_id:
            raise ValueError("unknown or stale human review request")
        decide_request(self.skill.workspace, self.skill.config, request_id, approved, reason)
        self.audit("review.decided", identity, request_id=request_id, approved=approved, reason=reason)
        return {"ok": True, "request_id": request_id, "approved": approved}

    def break_glass_issue(self, path, session_id, reason, ttl=900):
        identity = self.permissions.require_human("break_glass")
        nonempty(reason, "reason")
        relative = self._break_glass_path(path)
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 900:
            raise ValueError("break-glass ttl must be 1..900 seconds")
        token = secrets.token_urlsafe(32)
        row = {"path": relative, "session_id": session_id, "reason": reason,
               "expires_at": time.time() + ttl, "used": False, "operation": "write_file"}
        with self.store.transaction() as db:
            sessions = self.store.all(db, "session")
            if not any(s["session_id"] == session_id and not s["revoked"]
                       and s["expires_at"] > time.time() for s in sessions):
                raise ValueError("unknown or inactive target session")
            self.store.put(db, "break_glass", hashlib.sha256(token.encode()).hexdigest(), row)
        self.audit("permission.break_glass_issued", identity,
                   target_session_id=session_id, **{k: v for k, v in row.items() if k != "session_id"})
        return {"token": token, **row}

    def _break_glass_path(self, path):
        target = (self.skill.workspace / nonempty(path, "path")).resolve()
        relative = target.relative_to(self.skill.workspace).as_posix()
        if self.skill._in_gate_dir(target):
            raise PermissionError("break-glass 不允许修改门禁内部状态或策略")
        return relative

    def consume_break_glass(self, token, path):
        # Delegates an exact permission once. Stage/evidence/directory checks still run.
        self.permissions.policy()  # A delegation must not consume tokens under a broken policy.
        identity = self.permissions.identity()
        relative = self._break_glass_path(str(path))
        digest = hashlib.sha256(nonempty(token, "token").encode()).hexdigest()
        with self.store.transaction() as db:
            row = self.store.get(db, "break_glass", digest)
            if (not row or row["used"] or row["expires_at"] <= time.time()
                    or row["path"] != relative or row["session_id"] != identity["session_id"]):
                raise PermissionError("权限拒绝：break-glass 令牌无效、过期或不匹配")
            row["used"] = True
            self.store.put(db, "break_glass", digest, row)
        self.audit("permission.break_glass", identity, **{k: v for k, v in row.items()
                                                        if k != "session_id"})

    def snapshot(self):
        for operation in ["read_state", "read_diff", "read_evidence", "read_tasks", "read_goals", "read_audit"]:
            self.permissions.require(operation)
        self.skill.state.reload()
        with self.store.transaction() as db:
            diffs = self.store.all(db, "diff")
            tasks = self.store.all(db, "task")
            goals = self.store.all(db, "goal")
        from ..defense._common import read_json, evidence_path

        review = read_json(evidence_path(self.skill.workspace, self.skill.config,
                                        "human_review_request.json"))
        state = self.skill.state.snapshot()
        from ..defense import BUILTIN_DEFENSE_LINES
        checks = {}
        for entry in state["stage_history"]:
            for check in entry.get("evidence", {}).get("defense", {}).get("defense_checks", []):
                checks[check["line"]] = check
        log_path = self.skill.gate_dir / self.skill.config.audit_log_name
        if log_path.exists():
            import json
            from collections import deque
            with log_path.open(encoding="utf-8") as stream:
                recent = deque(stream, maxlen=100)
            for raw in recent:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if state.get("goal_id") and any(event.get(k) != state.get(k)
                                               for k in ("goal_id", "milestone_index")):
                    continue
                evidence = event.get("evidence", {})
                for check in evidence.get("defense_checks", evidence.get("defense", {}).get("defense_checks", [])):
                    checks[check["line"]] = check
        defense = [{"line": line.name, "status": "disabled" if not line.enabled(self.skill.config)
                    else "failed" if line.name in checks and not checks[line.name]["ok"]
                    else "passed" if checks.get(line.name, {}).get("ok") else "pending"}
                   for line in BUILTIN_DEFENSE_LINES]
        return {"state": state, "session": self.permissions.identity(),
                "tasks": tasks, "goals": goals, "diff": diffs[-1] if diffs else None,
                "pending_diffs": [d for d in diffs if d["status"] == "pending"],
                "human_review": review,
                "defense": defense}

    def dispatch(self, operation, data):
        # Explicit allowlist; HTTP cannot reach bootstrap, scheduler or state mutation internals.
        methods = {"task_capture": self.task_capture, "task_transition": self.task_transition,
                   "goal_create": self.goal_create, "goal_action": self.goal_action,
                   "diff_decide": self.diffs.decide, "review_decide": self.review_decide,
                   "session_issue": self.permissions.issue, "session_revoke": self.permissions.revoke,
                   "policy_update": self.permissions.update, "break_glass_issue": self.break_glass_issue,
                   "schedule_update": self.schedule_update}
        if not isinstance(operation, str):
            raise ValueError("operation must be text")
        if operation not in methods:
            raise ValueError("unknown control operation")
        if not isinstance(data, dict):
            raise ValueError("control data must be an object")
        import inspect
        try:
            inspect.signature(methods[operation]).bind(**data)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return {"ok": True, "result": methods[operation](**data)}

    def schedule_update(self, config):
        from .scheduler import validate_schedule
        from .storage import atomic_json
        identity = self.permissions.require_human("config_manage")
        validate_schedule(config)
        atomic_json(self.skill.gate_dir / "schedule.yaml", config)
        self.audit("scheduler.config_changed", identity, config=config)
        return {"ok": True, "restart_required": True}
