"""Scheduler lease tests with deterministic time and transport outcomes."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from community_grid.graph import GraphRuntime, digest, encode
from test_graph import unit


class Worker:
    def __init__(self, boot_id):
        self.boot_id = boot_id
        self.online = True
        self.drop_ack = False
        self.attempts = {}
        self.submits = 0

    def eligible(self, task):
        return self.online

    def lease_dispatch(self, lease, value):
        self.submits += 1
        self.attempts[lease["attempt"]] = "running"
        if self.drop_ack:
            self.drop_ack = False
            raise ConnectionError

    def lease_status(self, lease):
        if not self.online:
            raise ConnectionError
        status = self.attempts.get(lease["attempt"], "missing")
        data = encode("seed library")
        return {"lease": lease, "status": status, "data": data, "sha256": digest(data)}

    def lease_cancel(self, lease):
        if not self.online:
            raise ConnectionError
        self.attempts[lease["attempt"]] = "cancelled"


class LeaseTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "graph.db"
        self.now = 10
        self.a, self.b = Worker("worker-a"), Worker("worker-b")
        self.nodes = {"a": self.a, "b": self.b}
        self.runtime = self.open()
        self.graph = {"version": 1, "id": "g", "owner": "app", "timeout": 40,
                      "units": {"task": unit("text.normalize", "a", value=" seed    library ")}}
        self.graph["units"]["task"]["nodes"] = ["a", "b"]

    def open(self, boot="boot1", **kw):
        return GraphRuntime(self.path, self.nodes, lambda: self.now,
                            coordinator_boot_id=boot, **kw)

    def add(self, name="g"):
        self.runtime.submit(dict(self.graph, id=name), "app")

    def tick(self, name="g"):
        self.runtime.tick(name, "app")

    def state(self, name="g"):
        return self.runtime.status(name, "app")["units"]["task"]

    def publish(self, lease, node="a", data=None):
        data = encode("seed library") if data is None else data
        return self.runtime.publish("g", "app", "task", lease["attempt"], data, digest(data),
                                    lease=lease, worker_id=node)

    def test_assignment_persisted_and_publication_atomic(self):
        self.add()
        self.tick()
        lease = self.state()["lease"]
        self.assertEqual(lease, self.open().status("g", "app")["units"]["task"]["lease"])
        self.assertEqual((lease["deadline"], lease["generation"], lease["cpu"], lease["ram_mib"]),
                         (20, 1, 1, 128))
        self.assertEqual((lease["node"], lease["worker_boot_id"], lease["coordinator_boot_id"]),
                         ("a", "worker-a", "boot1"))
        self.assertEqual(lease["input_digest"], digest(encode(" seed    library ")))
        self.assertEqual(lease["request_digest"], digest(encode(
            {key: value for key, value in lease.items() if key != "request_digest"})))
        uuid.UUID(lease["attempt"])
        self.assertEqual(self.a.submits, 0)
        self.assertFalse(self.publish(lease, node="b"))
        for field, value in (("generation", True), ("worker_boot_id", "other"),
                             ("request_digest", "bad"), ("input_digest", "bad")):
            self.assertFalse(self.publish(dict(lease, **{field: value})))
        with self.assertRaises(ValueError):
            self.publish(lease, data=encode("wrong"))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TRIGGER reject_graph BEFORE UPDATE ON graphs
                          BEGIN SELECT RAISE(ABORT, 'forced transaction rollback'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.publish(lease)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM artifacts").fetchone(), (0,))
            db.execute("DROP TRIGGER reject_graph")
        self.assertEqual(self.state()["status"], "assigned")
        self.assertTrue(self.publish(lease))
        self.assertFalse(self.publish(lease))
        self.assertEqual(self.open().result("g", "app", "task"), "seed library")

    def test_lost_ack_reconciles_same_uuid_after_restart(self):
        self.a.drop_ack = True
        self.add()
        self.tick()
        lease = self.state()["lease"]
        self.tick()
        self.assertFalse(self.state().get("acknowledged", False))
        self.runtime = self.open()
        self.tick()
        self.a.attempts[lease["attempt"]] = "completed"
        self.tick()
        self.assertEqual(self.a.submits, 1)
        self.assertEqual(self.state()["lease"], lease)
        self.assertEqual(self.state()["status"], "completed")

    def test_expiry_reassigns_and_quarantine_blocks_capacity(self):
        self.add()
        self.tick()
        self.tick()
        old = self.state()["lease"]
        self.a.online = False
        self.now = 20
        self.assertFalse(self.publish(old))
        self.tick()
        new = self.state()["lease"]
        self.assertEqual((new["node"], new["generation"]), ("b", 2))
        self.assertNotEqual(new["attempt"], old["attempt"])
        self.assertFalse(self.publish(old))
        self.add("other")
        self.a.online = True
        self.a.lease_cancel = lambda lease: None  # running worker does not confirm cancellation
        self.tick("other")
        self.assertEqual(self.state("other")["status"], "waiting")
        self.a.attempts[old["attempt"]] = "completed"
        self.tick("other")
        self.assertEqual(self.state("other")["lease"]["node"], "a")

    def test_worker_and_coordinator_boot_fencing(self):
        self.add()
        self.tick()
        lease = self.state()["lease"]
        self.a.boot_id = "new-worker"
        self.assertFalse(self.publish(lease))
        self.tick()
        newer = self.state()["lease"]
        self.assertEqual(newer["generation"], 2)
        old_runtime = self.runtime
        self.runtime = self.open("boot2")
        self.assertEqual(self.state()["status"], "timed-out")
        with self.assertRaisesRegex(RuntimeError, "fenced"):
            old_runtime.status("g", "app")
        self.assertFalse(self.publish(newer))

    def test_memory_budget_and_cancel_quarantine(self):
        self.runtime = self.open(worker_memory_mib=64)
        self.add()
        self.tick()
        self.assertEqual(self.state()["status"], "waiting")
        self.runtime = self.open()
        self.tick()
        self.tick()
        self.a.online = False
        self.runtime.cancel("g", "app")
        self.tick()
        self.assertEqual(self.state()["status"], "cancelled")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT node FROM lease_quarantine").fetchone(), ("a",))

    def test_local_database_read_and_write_remain_compatible(self):
        local = GraphRuntime(self.path, self.nodes)
        local.submit(dict(self.graph, id="legacy"), "app")
        other = self.open("boot2")
        with self.assertRaises(KeyError):
            other.status("legacy", "app")
        self.assertEqual(local.status("legacy", "app")["units"]["task"]["status"], "waiting")
