"""Sidecar-owned periodic maintenance. YAML selects fixed handlers, never shell text."""
from __future__ import annotations

import math
import secrets
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from .storage import atomic_json

DEFAULTS = {"evidence_integrity_check": 3600, "red_team_fast": 604800,
            "btw_stale_cleanup": 86400, "goal_health_report": 86400,
            "residual_expiry_check": 86400}


def validate_schedule(config):
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("schedule requires version: 1")
    jobs = config.get("jobs", {})
    if not isinstance(jobs, dict):
        raise ValueError("schedule.jobs must be a mapping")
    for name, options in jobs.items():
        if name not in DEFAULTS or not isinstance(options, dict):
            raise ValueError("unknown scheduled task")
        if set(options) - {"interval_seconds", "stale_days", "enabled"}:
            raise ValueError("unknown schedule option")
        interval = options.get("interval_seconds", DEFAULTS[name])
        days = options.get("stale_days", 30)
        if (type(interval) not in {int, float} or not math.isfinite(interval) or interval <= 0
                or type(days) is not int or days < 1
                or type(options.get("enabled", True)) is not bool):
            raise ValueError("invalid schedule interval, stale_days or enabled")
    return jobs


class Scheduler:
    def __init__(self, skill, config):
        self.skill, self.control = skill, skill.control
        self.jobs = validate_schedule(config)
        self.owner = secrets.token_hex(12)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="gate-scheduler")
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception as exc:
                self.skill.logger.error("scheduler.failed", reason=str(exc))
            self._stop.wait(1)

    def run_pending(self, now=None):
        now = time.time() if now is None else now
        results = []
        for name, options in self.jobs.items():
            if self._stop.is_set() or not options.get("enabled", True):
                continue
            with self.control.store.transaction() as db:
                row = self.control.store.get(db, "schedule", name) or {"next_run": now}
                if row["next_run"] > now or row.get("lease_until", 0) > now:
                    continue
                row.update(owner=self.owner, lease_until=now + 360)
                self.control.store.put(db, "schedule", name, row)
            try:
                result = self.execute(name, options)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            with self.control.store.transaction() as db:
                row = self.control.store.get(db, "schedule", name)
                if row.get("owner") == self.owner:
                    row.update(next_run=now + options.get("interval_seconds", DEFAULTS[name]),
                               lease_until=0, last_result=result, last_run=now)
                    self.control.store.put(db, "schedule", name, row)
            self.skill.logger.info("scheduler.completed", task=name, result=result)
            results.append({"task": name, **result})
        return results

    def execute(self, name, options):
        if name == "evidence_integrity_check":
            self.skill.state.reload()  # verifies state HMAC, too
            ok, violations = self.skill.verify_evidence()
            return {"ok": ok, "violations": violations}
        if name == "btw_stale_cleanup":
            cutoff, changed = time.time() - options.get("stale_days", 30) * 86400, []
            with self.control.store.transaction() as db:
                for task in self.control.store.all(db, "task"):
                    if (task["type"] == "btw" and task["status"] in {"captured", "queued"}
                            and task["updated_at"] < cutoff and not task["stale"]):
                        task["stale"] = True
                        self.control.store.put(db, "task", task["task_id"], task)
                        changed.append(task["task_id"])
            return {"ok": True, "marked_stale": changed}
        if name == "residual_expiry_check":
            path = self.skill.workspace / "benchmarks/red_team/known_residual.yaml"
            if not path.exists():
                return {"ok": True, "skipped": "no residual ledger in workspace"}
            ledger = yaml.safe_load(path.read_text(encoding="utf-8"))
            today = datetime.now(timezone.utc).date()
            expired = [{**r, "expires_at": str(r["expires_at"])} for r in ledger["entries"]
                       if date.fromisoformat(str(r["expires_at"])) < today]
            return {"ok": not expired, "expired": expired}
        if name == "goal_health_report":
            with self.control.store.transaction() as db:
                goals = self.control.store.all(db, "goal")
                tasks = self.control.store.all(db, "task")
            report = {"ok": True, "generated_at": time.time(), "goals": goals,
                      "stale_tasks": [t["task_id"] for t in tasks if t["stale"]]}
            atomic_json(self.skill.gate_dir / "reports" / "goal-health.json", report)
            return report
        if name == "red_team_fast":
            # Uses the trusted installed source tree, not a script chosen by the Agent.
            script = Path(__file__).resolve().parents[2] / "benchmarks/red_team/run_red_team.py"
            if not script.is_file():
                raise ValueError("red_team_fast requires a phase-barrier source checkout")
            proc = subprocess.run([sys.executable, str(script), "--techniques",
                                   "equivalent_op,timing_evade", "--json", "--fail-on-escape"],
                                  cwd=script.parents[2], timeout=300, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                    "output": (proc.stdout + proc.stderr)[-8000:]}
        raise ValueError("unknown scheduled task")
