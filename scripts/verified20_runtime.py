"""Stop a verified batch durably, including when Docker cleanup partially fails."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def save_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _host_path(value):
    """Match Windows bind sources and Docker Desktop's host mount spelling."""
    text = str(value).replace("\\", "/").rstrip("/").casefold()
    for prefix in ("/run/desktop/mnt/host/", "/host_mnt/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            if len(text) > 1 and text[1] == "/":
                text = text[0] + ":" + text[1:]
            break
    return text


def owns_container(info, root):
    """Use this batch's bind paths; labels alone could match another batch."""
    target = _host_path(Path(root).resolve())
    return any(
        source == target or source.startswith(target + "/")
        for source in (_host_path(m.get("Source", "")) for m in info.get("Mounts", []))
    )


def stop_verified_batch(root, pid, reason, *, pid_probe, run=subprocess.run):
    """Always persist the stop, and isolate cleanup errors to each resource.

    Docker JSON stays as bytes so Windows' active code page cannot corrupt it.
    Command output is deliberately omitted from error records: inspect can
    contain credentials, and the failure type is enough for a cleanup review.
    """
    root = Path(root).resolve()
    now = datetime.now(timezone.utc).isoformat()
    result = {"reason": reason, "observed_at": now, "runner_pid": pid,
              "resume_requires_review": True, "cleanup_errors": [], "removed_containers": []}
    save_json(root / "halt.json", result)
    path = root / "status.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(state="halting", halt_reason=reason, stopped_at=now)
    save_json(path, current)

    def call(cmd):
        proc = run(cmd, capture_output=True, timeout=30)
        if proc.returncode:
            raise RuntimeError("command_exit_" + str(proc.returncode))
        return proc.stdout

    try:
        if pid_probe(pid):
            if sys.platform == "win32":
                call(["taskkill", "/PID", str(pid), "/T", "/F"])
            else:
                os.kill(pid, 15)
    except Exception as exc:
        result["cleanup_errors"].append({"operation": "stop_runner", "error": type(exc).__name__})
    try:
        identifiers = call(["docker", "ps", "-q"]).decode("ascii").split()
    except Exception as exc:
        identifiers = []
        result["cleanup_errors"].append({"operation": "list_containers", "error": type(exc).__name__})
    for identifier in identifiers:
        try:
            info = json.loads(call(["docker", "inspect", identifier]))[0]
            if owns_container(info, root):
                call(["docker", "rm", "-f", identifier])
                result["removed_containers"].append(identifier)
        except Exception as exc:
            result["cleanup_errors"].append({"operation": "cleanup_container", "container": identifier,
                                             "error": type(exc).__name__})
    result["runner_alive_after_stop"] = pid_probe(pid)
    result["cleanup_complete"] = not result["cleanup_errors"] and not result["runner_alive_after_stop"]
    save_json(root / "halt.json", result)
    current.update(state="halted", cleanup_complete=result["cleanup_complete"],
                   runner_alive_after_stop=result["runner_alive_after_stop"])
    save_json(path, current)
    return result
