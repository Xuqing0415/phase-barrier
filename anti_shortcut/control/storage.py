"""Small transactional store shared by tasks, sessions, reviews and schedules."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..state import _replace_with_retry


def atomic_json(path: Path, value: dict) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".control-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(Path(name), path)
    finally:
        Path(name).unlink(missing_ok=True)


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self):
        # One connection per transaction: safe across HTTP and scheduler threads.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS records "
                       "(kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db, kind, key):
        row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def put(db, kind, key, value):
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                   (kind, key, json.dumps(value, ensure_ascii=False)))

    @staticmethod
    def all(db, kind):
        return [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,))]
