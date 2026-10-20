"""Opt-in, deny-by-default policy. Roles come from server-issued session tokens."""
from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from fnmatch import fnmatch

import yaml

from ..state import _file_lock
from .storage import Store, atomic_json

READ = {"read_state", "read_evidence", "read_diff", "read_audit", "read_tasks", "read_goals"}
WORK = READ | {"write_file", "execute_command", "advance_stage", "record_test_run",
               "record_source_change", "task_capture", "task_claim", "task_complete"}
MAINTAIN = WORK | {"task_manage", "goal_manage", "review_decide", "config_manage"}
ROLES = {"observer": READ, "worker": WORK, "maintainer": MAINTAIN,
         "admin": MAINTAIN | {"policy_manage", "session_manage", "break_glass"}}
LEGACY = READ | {"write_file", "execute_command", "advance_stage", "record_test_run",
                 "record_source_change"}  # New control writes require explicit enrollment.


class Permissions:
    def __init__(self, gate_dir, store: Store, logger, token=None):
        self.path = gate_dir / "permissions.yaml"
        self.marker = gate_dir / "permissions.enabled"
        self.store, self.logger = store, logger
        self._token = ContextVar("control_session", default=token)

    @contextmanager
    def session(self, token):
        previous = self._token.set(token)
        try:
            yield
        finally:
            self._token.reset(previous)

    @property
    def enabled(self):
        return self.path.exists() or self.marker.exists()

    def policy(self):
        if not self.enabled:
            return {}
        try:
            result = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            self.validate(result)
            return result
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise PermissionError("权限拒绝：权限策略缺失或损坏") from exc

    @staticmethod
    def validate_pattern(pattern):
        # Policies use workspace-relative POSIX globs on every operating system.
        if (not isinstance(pattern, str) or not pattern.strip() or pattern != pattern.strip()
                or pattern.startswith('/') or '\\' in pattern or ':' in pattern
                or any(part in {'', '.', '..'} for part in pattern.split('/'))):
            raise ValueError("path pattern must be a nonempty relative POSIX glob")
        index = 0
        while index < len(pattern):
            if pattern[index] == '[':
                end = pattern.find(']', index + 1)
                if end < 0 or pattern[index + 1:end] in {'', '!'}:
                    raise ValueError("path pattern has an invalid character class")
                index = end
            elif pattern[index] == ']':
                raise ValueError("path pattern has an unmatched character class")
            index += 1

    @staticmethod
    def validate(policy):
        if not isinstance(policy, dict) or type(policy.get("version")) is not int or policy["version"] != 1:
            raise ValueError("permissions policy requires version: 1")
        if set(policy) - {"version", "roles", "write_paths", "review_writes", "rate_limits"}:
            raise ValueError("unknown permissions policy field")
        roles = policy.get("roles", {})
        if not isinstance(roles, dict):
            raise ValueError("roles must be a mapping")
        for role, operations in roles.items():
            if role not in ROLES or not isinstance(operations, list):
                raise ValueError("invalid role permissions")
            if any(not isinstance(op, str) or op not in ROLES[role] for op in operations):
                raise ValueError("a role may only restrict its built-in capabilities")
        scopes = policy.get("write_paths", {})
        if not isinstance(scopes, dict) or any(
            role not in ROLES or not isinstance(paths, list)
            or any(not isinstance(p, str) for p in paths) for role, paths in scopes.items()
        ):
            raise ValueError("write_paths must map roles to path lists")
        reviews = policy.get("review_writes", [])
        if not isinstance(reviews, list) or any(not isinstance(p, str) for p in reviews):
            raise ValueError("review_writes must be a list of path patterns")
        for patterns in [*scopes.values(), reviews]:
            for pattern in patterns:
                Permissions.validate_pattern(pattern)
        limits = policy.get("rate_limits", {})
        if not isinstance(limits, dict) or any(
            operation not in ROLES["admin"] or type(limit) is not int or limit < 1
            for operation, limit in limits.items()
        ):
            raise ValueError("rate_limits must map operations to positive per-minute integers")

    def identity(self):
        if not self.enabled:
            return {"session_id": "legacy", "role": "worker", "actor": "agent"}
        token = self._token.get()
        if not isinstance(token, str) or not token:
            raise PermissionError("权限拒绝：需要会话令牌")
        with self.store.transaction() as db:
            row = self.store.get(db, "session", hashlib.sha256(token.encode()).hexdigest())
        if not row or row.get("revoked") or row["expires_at"] <= time.time():
            raise PermissionError("权限拒绝：无效、撤销或过期的会话")
        return row

    def require(self, operation, target=None):
        try:
            policy = self.policy()
            identity = self.identity()
            allowed = LEGACY if not self.enabled else set(
                policy.get("roles", {}).get(identity["role"], ROLES[identity["role"]]))
            if operation not in allowed:
                raise PermissionError(f"权限拒绝：{identity['role']} 不允许 {operation}")
            scopes = policy.get("write_paths", {}).get(identity["role"])
            if operation == "write_file" and scopes is not None:
                if not target or not any(fnmatch(target, p) for p in scopes):
                    raise PermissionError("权限拒绝：目标不在会话允许的写入范围")
            limit = policy.get("rate_limits", {}).get(operation)
            if limit:
                now = time.time()
                key = identity["session_id"] + ":" + operation
                with self.store.transaction() as db:
                    row = self.store.get(db, "rate", key) or {"start": now, "count": 0}
                    if now - row["start"] >= 60:
                        row = {"start": now, "count": 0}
                    if row["count"] >= limit:
                        raise PermissionError("权限拒绝：操作超过会话每分钟速率限制")
                    row["count"] += 1
                    self.store.put(db, "rate", key, row)
            return identity
        except PermissionError as exc:
            self.logger.warning("permission.denied", operation=operation, target=target, reason=str(exc))
            raise

    def require_human(self, operation):
        identity = self.require(operation)
        if identity["actor"] != "human":
            self.logger.warning("permission.denied", operation=operation, reason="human session required")
            raise PermissionError("权限拒绝：此操作需要人工会话")
        return identity

    def bootstrap(self):
        # Trusted local provisioning only; never expose this method over HTTP or Agent tools.
        with _file_lock(self.path.with_suffix(".lock")):
            if self.enabled:
                raise ValueError("权限已初始化，不能再次 bootstrap")
            token = secrets.token_urlsafe(32)
            with self.store.transaction() as db:
                self._new_session(db, token, "admin", "human", 86400)
            atomic_json(self.path, {"version": 1, "roles": {}, "review_writes": []})
            self.marker.write_text("1\n", encoding="utf-8")
            self.logger.info("permission.bootstrapped", role="admin")
            return token

    def _new_session(self, db, token, role, actor, ttl):
        row = {"session_id": secrets.token_hex(12), "role": role, "actor": actor,
               "expires_at": time.time() + ttl, "revoked": False}
        self.store.put(db, "session", hashlib.sha256(token.encode()).hexdigest(), row)
        return row

    def issue(self, role, actor="agent", ttl=86400):
        admin = self.require_human("session_manage")
        if role not in ROLES or actor not in {"agent", "human"}:
            raise ValueError("invalid role or actor")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 604800:
            raise ValueError("ttl must be 1..604800 seconds")
        if role == "admin" and actor != "human":
            raise ValueError("admin sessions must belong to humans")
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            row = self._new_session(db, token, role, actor, ttl)
        self.logger.info("permission.session_issued", by=admin["session_id"], **row)
        return {**row, "token": token}

    def update(self, policy):
        admin = self.require_human("policy_manage")
        self.validate(policy)
        with _file_lock(self.path.with_suffix(".lock")):
            atomic_json(self.path, policy)
        self.logger.info("permission.policy_changed", session_id=admin["session_id"], policy=policy)

    def revoke(self, session_id):
        admin = self.require_human("session_manage")
        with self.store.transaction() as db:
            for row in db.execute("SELECT id,data FROM records WHERE kind='session'").fetchall():
                import json
                session = json.loads(row["data"])
                if session["session_id"] == session_id:
                    session["revoked"] = True
                    self.store.put(db, "session", row["id"], session)
                    break
            else:
                raise ValueError("unknown session")
        self.logger.info("permission.session_revoked", by=admin["session_id"], session_id=session_id)
