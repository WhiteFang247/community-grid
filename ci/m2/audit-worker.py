"""Read-only, credential-free extraction of guest execution evidence."""
from contextlib import closing
import json
import sqlite3

db = "/var/lib/cgos-worker/worker.db"
with closing(sqlite3.connect("file:" + db + "?mode=ro", uri=True)) as conn:
    rows = conn.execute("SELECT id,lease,state,outcome FROM worker_v2_attempts ORDER BY rowid").fetchall()
for attempt, lease, state, outcome in rows:
    lease = json.loads(lease)
    print("M2_WORKER " + json.dumps(dict(
        attempt=attempt, graph=lease["graph"], task=lease["task"], node=lease["node"],
        generation=lease["generation"], boot=lease["worker_boot_id"],
        input_digest=lease["input_digest"], status=state,
        outcome=json.loads(outcome) if outcome else None)))
assert all(row[2] not in ("queued", "running") for row in rows)
