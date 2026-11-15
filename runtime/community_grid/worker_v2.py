"""Version 2 worker admission and an isolated Linux reference executor."""
import argparse
from contextlib import closing, contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from .graph import OPERATIONS, digest, encode

LIMIT = 65536
OPS = {"text.normalize", "text.sha256"}
TERMINAL = {"completed", "failed", "cancelled", "timed-out"}
LEASE_FIELDS = {"graph", "task", "node", "worker_boot_id", "coordinator_boot_id",
                "generation", "attempt", "deadline", "budget_seconds", "cpu", "ram_mib",
                "operation", "input_digest", "request_digest"}


class Worker:
    def __init__(self, database, coordinator, node, boot_id, memory_mib=128, clock=time.monotonic):
        if type(memory_mib) is not int or not 16 <= memory_mib <= 128:
            raise ValueError("Worker memory cap must be 16â€“128 MiB")
        if not all(isinstance(x, str) and x and len(x) <= 256 for x in (coordinator, node, boot_id)):
            raise ValueError("Invalid worker identity")
        self.database = Path(database).resolve()
        self.coordinator, self.node, self.boot_id = coordinator, node, boot_id
        self.memory_mib, self.clock = memory_mib, clock
        self.stop_requested = False
        with self.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS worker_v2_attempts (
                coordinator TEXT NOT NULL, id TEXT NOT NULL, lease BLOB NOT NULL,
                value BLOB, state TEXT NOT NULL, expires REAL NOT NULL, cancel INTEGER NOT NULL DEFAULT 0,
                outcome BLOB, PRIMARY KEY(coordinator,id))""")

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.database, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            yield db

    def validate_lease(self, lease):
        if not isinstance(lease, dict) or set(lease) != LEASE_FIELDS:
            raise ValueError("Invalid lease fields")
        for key in ("graph", "task", "coordinator_boot_id", "node", "worker_boot_id"):
            if not isinstance(lease[key], str) or not lease[key] or len(lease[key]) > 256:
                raise ValueError("Invalid lease identity")
        if lease["node"] != self.node or lease["worker_boot_id"] != self.boot_id:
            raise ValueError("Lease is for a different worker instance")
        if type(lease["generation"]) is not int or lease["generation"] < 1:
            raise ValueError("Invalid generation")
        if not isinstance(lease["attempt"], str) or str(uuid.UUID(lease["attempt"])) != lease["attempt"]:
            raise ValueError("Invalid attempt UUID")
        for key in ("deadline", "budget_seconds"):
            if type(lease[key]) not in (int, float) or not math.isfinite(lease[key]):
                raise ValueError("Invalid deadline or budget")
        if not 0 < lease["budget_seconds"] <= 300:
            raise ValueError("Invalid execution budget")
        if (type(lease["cpu"]) is not int or lease["cpu"] != 1
                or type(lease["ram_mib"]) is not int or not 1 <= lease["ram_mib"] <= self.memory_mib
                or not isinstance(lease["operation"], str) or lease["operation"] not in OPS):
            raise ValueError("Unsupported operation or resource reservation")
        if (not isinstance(lease["input_digest"], str) or len(lease["input_digest"]) != 64
                or any(c not in "0123456789abcdef" for c in lease["input_digest"])):
            raise ValueError("Invalid input digest")
        fields = {k: v for k, v in lease.items() if k != "request_digest"}
        if digest(encode(fields)) != lease["request_digest"]:
            raise ValueError("Invalid canonical request digest")
        return encode(lease)

    def request(self, request):
        if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 2:
            raise ValueError("Unsupported worker protocol version")
        if len(encode(request)) > LIMIT:
            raise ValueError("Request exceeds wire limit")
        action = request.get("action")
        if action == "probe" and set(request) == {"version", "action"}:
            return {"version": 2, "node": self.node, "worker_boot_id": self.boot_id,
                    "cpu": 1, "slots": 1, "ram_mib": self.memory_mib, "operations": sorted(OPS)}
        if action not in ("submit", "status", "cancel"):
            raise ValueError("Unsupported action")
        if set(request) != ({"version", "action", "lease", "value"} if action == "submit"
                            else {"version", "action", "lease"}):
            raise ValueError("Invalid request fields")
        lease = request["lease"]
        encoded_lease = self.validate_lease(lease)
        key = (self.coordinator, lease["attempt"])
        value = None
        if action == "submit":
            if type(request["value"]) is not str:
                raise ValueError("Expected text input")
            value = encode(request["value"])
            if len(value) > LIMIT - 4096:
                raise ValueError("Input exceeds worker limit")
            if digest(value) != lease["input_digest"]:
                raise ValueError("Input digest mismatch")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM worker_v2_attempts WHERE coordinator=? AND id=?", key).fetchone()
            if row is None and action != "status":
                if action == "submit" and db.execute(
                    "SELECT count(*) FROM worker_v2_attempts WHERE coordinator=? AND state IN ('queued','running')",
                    (self.coordinator,),
                ).fetchone()[0] >= 32:
                    raise ValueError("Worker admission queue is full")
                state = "cancelled" if action == "cancel" else "queued"
                db.execute("""INSERT INTO worker_v2_attempts
                            (coordinator,id,lease,value,state,expires) VALUES (?,?,?,?,?,?)""",
                           (*key, encoded_lease, value, state, self.clock() + lease["budget_seconds"]))
                row = db.execute("SELECT * FROM worker_v2_attempts WHERE coordinator=? AND id=?", key).fetchone()
            if row is None:
                return {"version": 2, "lease": lease, "status": "missing"}
            if row["lease"] != encoded_lease or (value is not None and row["value"] is not None
                                                   and row["value"] != value):
                raise ValueError("Conflicting attempt UUID")
            if action == "cancel" and row["state"] not in TERMINAL:
                if row["state"] == "queued":
                    db.execute("UPDATE worker_v2_attempts SET state='cancelled' WHERE coordinator=? AND id=?", key)
                else:
                    db.execute("UPDATE worker_v2_attempts SET cancel=1 WHERE coordinator=? AND id=?", key)
                row = db.execute("SELECT * FROM worker_v2_attempts WHERE coordinator=? AND id=?", key).fetchone()
            reply = {"version": 2, "lease": lease, "status": row["state"]}
            if row["outcome"] is not None:
                reply.update(json.loads(row["outcome"]))
            return reply

    @contextmanager
    def executor(self):
        """Hold the Linux process lock through all work, including child teardown."""
        import fcntl
        with self.database.with_suffix(self.database.suffix + ".executor").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.transaction() as db:
                for row in db.execute("SELECT coordinator,id,lease,state,cancel FROM worker_v2_attempts"
                                      " WHERE state IN ('queued','running')").fetchall():
                    if row["state"] == "running" or json.loads(row["lease"])["worker_boot_id"] != self.boot_id:
                        outcome = {"reason": "executor ownership lost"}
                        db.execute("UPDATE worker_v2_attempts SET state=?,outcome=? WHERE coordinator=? AND id=?",
                                   ("cancelled" if row["cancel"] else "failed", encode(outcome),
                                    row["coordinator"], row["id"]))
            self._executor_fd = lock.fileno()
            try:
                yield self._executor_fd
            finally:
                self._executor_fd = None

    def run_one(self, lock_fd):
        if lock_fd != getattr(self, "_executor_fd", None):
            raise RuntimeError("Execution requires ownership of the worker lock")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM worker_v2_attempts WHERE coordinator=? AND state='queued'"
                             " ORDER BY rowid LIMIT 1", (self.coordinator,)).fetchone()
            if row is None:
                return False
            key = (row["coordinator"], row["id"])
            if self.clock() >= row["expires"]:
                db.execute("UPDATE worker_v2_attempts SET state='timed-out' WHERE coordinator=? AND id=?", key)
                return True
            db.execute("UPDATE worker_v2_attempts SET state='running' WHERE coordinator=? AND id=?", key)
        lease = json.loads(row["lease"])
        try:
            state, outcome = self.run_child(row, key, lease, lock_fd)
        except (OSError, ValueError, TypeError) as exc:
            state, outcome = "failed", {"reason": str(exc)[:256]}
        with self.transaction() as db:
            if db.execute("SELECT cancel FROM worker_v2_attempts WHERE coordinator=? AND id=?", key).fetchone()[0]:
                state = "cancelled"
            elif self.clock() >= row["expires"]:
                state = "timed-out"
            if state != "completed":
                outcome.pop("value", None)
                outcome.pop("sha256", None)
            db.execute("UPDATE worker_v2_attempts SET state=?,outcome=? WHERE coordinator=? AND id=?",
                       (state, encode(outcome), *key))
        return True

    def child_command(self, lease):
        return [sys.executable, "-m", "community_grid.worker_v2", "--child",
                lease["operation"], str(lease["ram_mib"]), str(os.getpid())]

    def run_child(self, row, key, lease, lock_fd):
        import resource
        started = self.clock()
        sampled_peak = 0
        state = "failed"
        result = {}
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
            stdin.write(row["value"])
            stdin.seek(0)
            proc = subprocess.Popen(self.child_command(lease), stdin=stdin, stdout=stdout,
                                    stderr=subprocess.DEVNULL, start_new_session=True,
                                    pass_fds=(lock_fd,))
            try:
                while True:
                    try:
                        for line in Path(f"/proc/{proc.pid}/status").read_text().splitlines():
                            if line.startswith("VmHWM:"):
                                sampled_peak = max(sampled_peak, int(line.split()[1]))
                    except FileNotFoundError:
                        pass
                    if proc.poll() is not None:
                        break
                    with closing(sqlite3.connect(self.database, timeout=0.1)) as db:
                        try:
                            cancelled = db.execute("SELECT cancel FROM worker_v2_attempts WHERE coordinator=? AND id=?",
                                                   key).fetchone()[0]
                        except sqlite3.OperationalError as exc:
                            if "locked" not in str(exc):
                                raise
                            cancelled = False
                    if cancelled or self.clock() >= row["expires"]:
                        state = "cancelled" if cancelled else "timed-out"
                        break
                    if self.stop_requested:
                        break
                    time.sleep(0.02)
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proc.wait()
            stdout.seek(0)
            raw = stdout.read(LIMIT + 1)
            if raw and len(raw) <= LIMIT:
                result = json.loads(raw)
            if state == "failed" and proc.returncode == 0 and "value" in result:
                state = "completed"
            outcome = {"elapsed_seconds": self.clock() - started,
                       "service_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       "child_peak_rss_kib": max(sampled_peak, result.get("peak_rss_kib", 0))}
            if state == "completed":
                outcome.update(value=result["value"], sha256=digest(encode(result["value"])))
            else:
                outcome["reason"] = result.get("reason", state)
        return state, outcome


def child(operation, memory_mib, parent):
    import resource
    # Parent death cannot leave the child holding the execution slot forever.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        return 2
    cap = memory_mib * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 301))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    try:
        if operation not in OPS:
            raise ValueError("Unsupported operation")
        value = json.loads(sys.stdin.buffer.read(LIMIT + 1))
        answer = {"value": OPERATIONS[operation][2](value)}
    except MemoryError:
        answer = {"reason": "memory limit exceeded"}
    answer["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sys.stdout.buffer.write(encode(answer))
    return 0 if "value" in answer else 1


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        return child(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--coordinator", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--memory-mib", type=int, default=128)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("The executor requires Linux")
    worker = Worker(args.database, args.coordinator, args.node,
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip(), args.memory_mib)
    def stop(*_):
        worker.stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with worker.executor() as lock:
        while not worker.stop_requested:
            if not worker.run_one(lock):
                time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
