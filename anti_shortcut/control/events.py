"""SDK independent event envelope with a byte cursor for incremental audit tailing."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Event:
    cursor: int
    type: str
    timestamp: str
    data: dict


def event_type(name):
    if name == "diff.preview":
        return "write.preview"
    if "denied" in name or "rejected" in name:
        return "tool.blocked"
    if name in {"proxy_write_ok", "proxy_exec_ok", "file_written", "diff.written"}:
        return "tool.complete"
    if name == "stage_advanced":
        return "stage.changed"
    if name in {"review.requested", "diff.review_requested"}:
        return "human_input_required"
    return name


def tail_events(path, cursor=0, limit=200):
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    events = []
    if not path.exists():
        return {"cursor": 0, "events": []}
    with path.open("rb") as stream:
        if cursor > stream.seek(0, 2):
            cursor = 0
        stream.seek(cursor)
        for _ in range(limit):
            start = stream.tell()
            line = stream.readline(1024 * 1024)
            if not line or not line.endswith(b"\n"):
                stream.seek(start)
                break
            cursor = stream.tell()
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(row, dict) and "event" in row:
                events.append(asdict(Event(cursor, event_type(row["event"]),
                                           row.get("timestamp", row.get("ts", "")), row)))
    return {"cursor": cursor, "events": events}
