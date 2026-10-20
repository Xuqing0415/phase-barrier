"""Pre-write previews and approvals bound to an exact file, content and gate cycle."""
from __future__ import annotations

import difflib
import hashlib
import json
import time
from fnmatch import fnmatch

from .storage import atomic_json


class ReviewRequired(PermissionError):
    pass


class DiffCache:
    def __init__(self, skill, store, permissions):
        self.skill, self.store, self.permissions = skill, store, permissions
        self.path = skill.gate_dir / "diff" / "latest.json"

    def preview(self, path, content):
        target = self.skill.workspace / path
        target = target.resolve()
        relative = target.relative_to(self.skill.workspace).as_posix()
        if not isinstance(content, (str, bytes)):
            raise ValueError("diff requires text or bytes content")
        old = target.read_bytes() if target.exists() else b""
        new = content.encode("utf-8") if isinstance(content, str) else content
        state = self.skill.state.snapshot()
        identity = self.permissions.identity()
        basis = {"path": relative, "old_hash": hashlib.sha256(old).hexdigest(),
                 "new_hash": hashlib.sha256(new).hexdigest(), "existed": target.exists(),
                 "stage": state["current_stage"], "goal_id": state.get("goal_id"),
                 "milestone_index": state.get("milestone_index"),
                 "session_id": identity["session_id"], "history": state["stage_history"]}
        review_id = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
        binary = b"\x00" in old or b"\x00" in new
        oversized = max(len(old), len(new)) > 2 * 1024 * 1024
        text = "Binary content" if binary else "Content exceeds 2 MiB preview limit" if oversized else "".join(
            difflib.unified_diff(old.decode("utf-8", errors="replace").splitlines(True),
                                 new.decode("utf-8", errors="replace").splitlines(True),
                                 fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        preview = {k: v for k, v in basis.items() if k != "history"}
        preview.update(review_id=review_id, diff=text, binary=binary, truncated=oversized,
                       timestamp=time.time(), source_tool="write_file", status="preview")
        if not binary and not oversized:
            preview.update(old_content=old.decode("utf-8", errors="replace"),
                           new_content=new.decode("utf-8", errors="replace"))
        patterns = self.permissions.policy().get("review_writes", [])
        required = any(fnmatch(relative, p) for p in patterns)
        with self.store.transaction() as db:
            previous = self.store.get(db, "diff", review_id)
            if required:
                preview["status"] = (previous or {}).get("status", "pending")
                if preview["status"] == "consumed":
                    preview["status"] = "pending"
            self.store.put(db, "diff", review_id, preview)
        atomic_json(self.path, preview)
        self.skill.logger.info("diff.preview", path=relative, review_id=review_id,
                               status=preview["status"], stage=preview["stage"])
        if required and preview["status"] != "approved":
            self.skill.logger.info("diff.review_requested", review_id=review_id, path=relative)
            raise ReviewRequired(f"需要人工复核 diff {review_id}（{preview['status']}），批准后重试原写入")
        return preview

    def consumed(self, preview):
        with self.store.transaction() as db:
            row = self.store.get(db, "diff", preview["review_id"])
            row["status"] = "consumed"
            self.store.put(db, "diff", row["review_id"], row)
        atomic_json(self.path, row)
        self.skill.logger.info("diff.written", path=row["path"], review_id=row["review_id"])

    def decide(self, review_id, approved, reason):
        identity = self.permissions.require_human("review_decide")
        if type(approved) is not bool or not isinstance(reason, str) or not reason.strip():
            raise ValueError("review decision requires a boolean and a nonempty reason")
        with self.store.transaction() as db:
            row = self.store.get(db, "diff", review_id)
            if not row or row["status"] != "pending":
                raise ValueError("diff is not pending review")
            row.update(status="approved" if approved else "rejected", reason=reason,
                       reviewed_by=identity["session_id"])
            self.store.put(db, "diff", review_id, row)
        self.skill.logger.info("diff.reviewed", review_id=review_id, approved=approved,
                               reason=reason, session_id=identity["session_id"])
        return row
