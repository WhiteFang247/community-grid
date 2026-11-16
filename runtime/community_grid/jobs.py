"""Local-only task queue with explicit allowed operations, no shell execution."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ALLOWED = {"hardware.discover", "model.recommend", "system.health"}
PROMPT_LIMIT = 4096

class JobQueue:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS job_state ON jobs(kind,status,created_at)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name in ("started_at", "finished_at"):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def submit(self, kind: str, payload: dict | None = None) -> str:
        if kind not in ALLOWED:
            raise ValueError("unsupported job kind")
        job_id = str(uuid.uuid4())
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO jobs (id,kind,status,payload,result,created_at) VALUES (?,?,?,?,?,?)",
                (job_id, kind, "queued", json.dumps(payload or {}), None,
                 datetime.now(timezone.utc).isoformat()))
        return job_id

    def submit_inference(self, prompt, job_id=None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > PROMPT_LIMIT:
            raise ValueError(f"Provide a nonempty prompt of at most {PROMPT_LIMIT} UTF-8 bytes")
        job_id = str(uuid.UUID(job_id)) if job_id is not None else str(uuid.uuid4())
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT kind,payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                if existing[0] != "inference" or json.loads(existing[1]) != {"prompt": prompt}:
                    raise ValueError("Job ID already belongs to different work")
                return job_id
            pending = db.execute("SELECT count(*) FROM jobs WHERE kind='inference' AND status='queued'").fetchone()[0]
            if pending >= 32:
                raise OverflowError("Inference queue full")
            db.execute("INSERT INTO jobs (id,kind,status,payload,created_at) VALUES (?,'inference','queued',?,?)",
                       (job_id, json.dumps({"prompt": prompt}), datetime.now(timezone.utc).isoformat()))
        return job_id

    def claim_inference(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE kind='inference' AND status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), row[0]))
        return self.get(row[0])

    def inference_busy(self):
        with closing(self._connect()) as db:
            return db.execute("""SELECT 1 FROM jobs WHERE kind='inference'
                AND status IN ('queued','running') LIMIT 1""").fetchone() is not None

    def cancel_inference(self, job_id):
        with closing(self._connect()) as db, db:
            return bool(db.execute("""UPDATE jobs SET status='cancelled',finished_at=?
                WHERE id=? AND kind='inference' AND status IN ('queued','running')""",
                (datetime.now(timezone.utc).isoformat(), job_id)).rowcount)

    def finish_inference(self, job_id, status, result):
        with closing(self._connect()) as db, db:
            db.execute("""UPDATE jobs SET status=?,result=?,finished_at=?
                WHERE id=? AND kind='inference' AND status='running'""",
                (status, json.dumps(result), datetime.now(timezone.utc).isoformat(), job_id))

    def recover_inference(self):
        with closing(self._connect()) as db, db:
            return db.execute("""UPDATE jobs SET status='failed',result=?,finished_at=?
                WHERE kind='inference' AND status='running'""",
                (json.dumps({"error": "Worker interrupted before recording a result"}),
                 datetime.now(timezone.utc).isoformat())).rowcount

    def run(self, job_id: str, handler):
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT kind,payload,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            kind, payload, status = row
            if kind not in ALLOWED:
                raise ValueError("unsupported job kind")
            if status != "queued":
                raise ValueError("job is not queued")
            claimed = db.execute("UPDATE jobs SET status='running' WHERE id=? AND status='queued'", (job_id,))
            if claimed.rowcount != 1:
                raise ValueError("job was already claimed")
        try:
            result = handler(kind, json.loads(payload))
            status = "done"
        except Exception as exc:
            result, status = {"error": str(exc)}, "failed"
        with closing(self._connect()) as db, db:
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=?",
                       (status, json.dumps(result), job_id))
        return self.get(job_id)

    def get(self, job_id: str):
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        data["result"] = json.loads(data["result"]) if data["result"] else None
        return data

    def list_jobs(self, limit: int = 30):
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall()
        return [self.get(row[0]) for row in rows]
