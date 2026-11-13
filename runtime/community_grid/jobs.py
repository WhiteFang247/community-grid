"""Local-only task queue with explicit allowed operations, no shell execution."""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ALLOWED = {"hardware.discover", "model.recommend", "system.health"}

class JobQueue:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def submit(self, kind: str, payload: dict | None = None) -> str:
        if kind not in ALLOWED:
            raise ValueError("unsupported job kind")
        job_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (job_id, kind, "queued", json.dumps(payload or {}), None,
                 datetime.now(timezone.utc).isoformat()))
        return job_id

    def run(self, job_id: str, handler):
        with self._connect() as db:
            row = db.execute("SELECT kind,payload,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            kind, payload, status = row
            if status != "queued":
                raise ValueError("job is not queued")
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        try:
            result = handler(kind, json.loads(payload))
            status = "done"
        except Exception as exc:
            result, status = {"error": str(exc)}, "failed"
        with self._connect() as db:
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=?",
                       (status, json.dumps(result), job_id))
        return self.get(job_id)

    def get(self, job_id: str):
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        data["result"] = json.loads(data["result"]) if data["result"] else None
        return data

    def list_jobs(self, limit: int = 30):
        with self._connect() as db:
            rows = db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall()
        return [self.get(row[0]) for row in rows]
