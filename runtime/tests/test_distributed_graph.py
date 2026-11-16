"""Deterministic coordinator, v2 protocol, and durable admission tests."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from community_grid.enrollment import Peer
from community_grid.graph import OPERATIONS, digest, encode
from community_grid.native import Runtime, coordinator_owner
from community_grid.worker_v2 import Worker
from test_enrollment import Inline

GRAPH = Path(__file__).resolve().parents[2] / "distro/m2/reference-graph.json"


class Lab:
    def __init__(self, root):
        self.root, self.now = root, 100
        self.workers = {n: Worker(root / (n + ".db"), "coord", n, "boot-" + n,
                                 clock=lambda: self.now) for n in ("A", "B")}
        self.disconnected, self.holds = set(), set()
        self.dropped = self.delayed = self.interrupted = None
        self.drop_task = self.delay_task = self.interrupt_task = None
        self.executed = {n: [] for n in self.workers}
        self.peers = {
            n: Peer(dict(node=n, operations={"text.normalize": 1, "text.sha256": 1}, memory_mib=128),
                    lambda request, name=n: self.relay(name, request), Inline(), lambda: self.now)
            for n in self.workers
        }
        self.runtime = Runtime(root, self, boot_id="coord-boot")
        self.runtime.graphs.clock = lambda: self.now

    def load(self, graphs):
        graphs.set_enrollment({n: "test-pin-" + n for n in self.peers})

    def tick(self):
        for peer in self.peers.values():
            peer.poll()

    def refresh(self, leased):
        for name, peer in self.peers.items():
            peer.refresh(leased=name in leased)

    def relay(self, name, request):
        if name in self.disconnected:
            raise ConnectionError("deterministic offline barrier")
        if name == "B" and request["action"] == "submit":
            lease = request["lease"]
            if self.delay_task == lease["task"] and self.delayed is None:
                self.delayed = request
                raise ConnectionError("submission held before admission")
        reply = self.workers[name].request(request)
        if name == "B" and request["action"] == "submit":
            if self.drop_task == lease["task"] and self.dropped is None:
                self.dropped = lease
                raise ConnectionError("drop acknowledgment after durable admission")
            if self.interrupt_task == lease["task"] and self.interrupted is None:
                self.interrupted = lease
                self.disconnected.add(name)
                raise ConnectionError("disconnect after durable admission")
        return reply

    def execute_durable_queue(self):
        # Controlled executor stand-in. Admission, terminal retrieval, tombstones,
        # scheduler and async peer adapter are the real implementation.
        for name, worker in self.workers.items():
            if name in self.holds:
                continue
            with worker.transaction() as db:
                rows = db.execute("SELECT * FROM worker_v2_attempts WHERE state='queued'").fetchall()
                for row in rows:
                    lease = json.loads(row["lease"])
                    value = OPERATIONS[lease["operation"]][2](json.loads(row["value"]))
                    outcome = {"value": value, "sha256": digest(encode(value))}
                    self.executed[name].append(lease["attempt"])
                    db.execute("UPDATE worker_v2_attempts SET state='completed',outcome=?"
                               " WHERE coordinator=? AND id=?", (encode(outcome), row["coordinator"], row["id"]))

    def until(self, condition, execute=True):
        for _ in range(45):
            self.runtime.tick()
            if condition():
                return
            if execute:
                self.execute_durable_queue()
        raise AssertionError("deterministic checkpoint did not occur")

    def submit(self, graph):
        return self.runtime.request({"action": "submit", "graph": graph})

    def unit(self, task, graph="m2-reference"):
        return self.runtime.graphs.status(graph, "local")["units"][task]

    def quarantined(self, lease):
        with closing(sqlite3.connect(self.runtime.path)) as db:
            return bool(db.execute("SELECT 1 FROM lease_quarantine WHERE attempt=?",
                                   (lease["attempt"],)).fetchone())

    def verify_reference(self):
        values = {"normalize": "seed library"}
        values["hash"] = hashlib.sha256(values["normalize"].encode()).hexdigest()
        values["final"] = hashlib.sha256(values["hash"].encode()).hexdigest()
        for name, node in (("normalize", "A"), ("hash", "B"), ("final", "A")):
            task = self.unit(name)
            assert task["node"] == node
            assert self.runtime.graphs.result("m2-reference", "local", name) == values[name]
            expected = hashlib.sha256(json.dumps(values[name]).encode()).hexdigest()
            assert task["result"]["sha256"] == expected
            assert len(task["history"]) == 1 and task["history"][0]["outcome"] == "completed"
        return values["final"]


def before_restart(root):
    lab = Lab(root)
    lab.drop_task = "hash"
    lab.submit(json.loads(GRAPH.read_text()))
    lab.until(lambda: lab.dropped is not None)
    (root / "lost.json").write_text(json.dumps(lab.dropped))


class DistributedTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.lab = Lab(self.root)

    def reference(self):
        return json.loads(GRAPH.read_text())

    def test_graph_cross_worker_artifacts_and_lost_ack(self):
        lab = self.lab
        lab.drop_task = "hash"
        lab.submit(self.reference())
        lab.until(lambda: lab.unit("final")["status"] == "completed")
        lab.verify_reference()
        self.assertEqual(lab.executed["B"], [lab.dropped["attempt"]])
        self.assertEqual(lab.unit("hash")["lease"], lab.dropped)

    def test_consecutive_graphs_complete_without_worker_slot_leaks(self):
        lab = self.lab
        for i in range(4):
            graph = dict(self.reference(), id=f"graph-{i}")
            lab.submit(graph)
            lab.until(lambda: lab.unit("final", graph["id"])["status"] == "completed")
            for name in graph["units"]:
                record = lab.unit(name, graph["id"])
                self.assertEqual(len(record["history"]), 1)
                self.assertFalse(lab.quarantined(record["lease"]))
            lab.now += 3
        attempts = lab.executed["A"] + lab.executed["B"]
        self.assertEqual(len(attempts), 12)
        self.assertEqual(len(set(attempts)), 12)

    def test_real_coordinator_process_restart_reconciles_durable_admission(self):
        subprocess.run([sys.executable, __file__, "--before-restart", str(self.root)], check=True, timeout=12)
        lab = Lab(self.root)
        lease = json.loads((self.root / "lost.json").read_text())
        self.assertEqual(lab.workers["B"].request({"version": 2, "action": "status", "lease": lease})["status"],
                         "queued")
        lab.until(lambda: lab.unit("final")["status"] == "completed")
        lab.verify_reference()
        self.assertEqual(lab.executed["B"], [lease["attempt"]])
        self.assertEqual(lab.unit("hash")["lease"], lease)

    def test_offline_expiry_fallback_fences_and_reconciles(self):
        lab = self.lab
        graph = self.reference()
        graph["units"]["final"]["nodes"] = ["A", "B"]
        lab.holds.add("B")
        lab.submit(graph)
        lab.until(lambda: lab.unit("hash")["status"] == "assigned")
        hold = dict(id="hold", units={"hold": dict(graph["units"]["normalize"])},
                    owner="local", version=1)
        lab.holds.add("A")
        lab.submit(hold)
        lab.until(lambda: lab.unit("hold", "hold")["status"] == "assigned")
        lab.holds.remove("B")
        lab.interrupt_task = "final"
        lab.until(lambda: lab.interrupted is not None)
        old = lab.interrupted
        self.assertEqual(old["node"], "B")
        self.assertEqual(lab.unit("hash")["status"], "completed")
        self.assertEqual(lab.unit("hash")["node"], "B")
        lab.execute_durable_queue()
        lab.now += 10
        lab.runtime.tick()
        self.assertFalse(lab.peers["B"].online())
        lab.runtime.request({"action": "graph-cancel", "id": "hold"})
        lab.until(lambda: lab.unit("hold", "hold")["status"] == "cancelled")
        lab.holds.clear()
        lab.now = old["deadline"]
        lab.until(lambda: lab.unit("final")["status"] == "completed")
        record = lab.unit("final")
        self.assertEqual((record["lease"]["node"], record["lease"]["generation"]), ("A", 2))
        self.assertNotEqual(record["attempt"], old["attempt"])
        self.assertTrue(lab.quarantined(old))
        self.assertEqual([h["outcome"] for h in record["history"]], ["timed-out", "completed"])
        late = lab.workers["B"].request({"version": 2, "action": "status", "lease": old})
        self.assertEqual(late["status"], "completed")
        self.assertFalse(lab.runtime.graphs.publish("m2-reference", "local", "final", old["attempt"],
                                                   encode(late["value"]), late["sha256"],
                                                   lease=old, worker_id="B"))
        result = hashlib.sha256(b"seed library").hexdigest()
        expected = hashlib.sha256(result.encode()).hexdigest()
        self.assertEqual(lab.runtime.graphs.result("m2-reference", "local", "final"), expected)
        self.assertEqual(record["result"]["sha256"], hashlib.sha256(json.dumps(expected).encode()).hexdigest())
        lab.disconnected.clear()
        lab.now += 2
        lab.until(lambda: not lab.quarantined(old))

    def test_corrupt_dependency_fails_closed_without_dispatch(self):
        lab = self.lab
        lab.disconnected.add("B")
        lab.submit(self.reference())
        lab.until(lambda: lab.unit("normalize")["status"] == "completed")
        with closing(sqlite3.connect(lab.runtime.path)) as db, db:
            db.execute("UPDATE artifacts SET data=? WHERE hash=?",
                       (b'broken', lab.unit("normalize")["result"]["sha256"]))
        lab.disconnected.clear()
        lab.now += 2
        lab.until(lambda: lab.unit("hash")["status"] == "failed")
        self.assertEqual(lab.unit("hash")["history"], [])
        lab.until(lambda: lab.unit("final")["status"] == "blocked")
        self.assertEqual(lab.executed["B"], [])

    @unittest.skipUnless(sys.platform == "linux", "Linux process ownership lock")
    def test_one_coordinator_owner_per_state_directory(self):
        with coordinator_owner(self.root, True):
            with self.assertRaises(BlockingIOError):
                with coordinator_owner(self.root, True):
                    self.fail("second owner acquired the same state")
        with coordinator_owner(self.root, True):
            pass

    def test_delayed_admission_after_cancel_hits_tombstone(self):
        lab = self.lab
        graph = self.reference()
        lab.delay_task = "hash"
        lab.submit(graph)
        lab.until(lambda: lab.delayed is not None)
        old = lab.delayed["lease"]
        lab.runtime.request({"action": "graph-cancel", "id": "m2-reference"})
        lab.until(lambda: lab.unit("hash")["status"] == "cancelled" and not lab.quarantined(old))
        reply = lab.workers["B"].request(lab.delayed)
        self.assertEqual(reply["status"], "cancelled")
        lab.execute_durable_queue()
        self.assertEqual(lab.executed["B"], [])
        data = encode("anything")
        self.assertFalse(lab.runtime.graphs.publish("m2-reference", "local", "hash", old["attempt"],
                                                   data, digest(data), lease=old, worker_id="B"))
        self.assertEqual(lab.unit("final")["status"], "cancelled")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--before-restart":
        before_restart(Path(sys.argv[2]))
    else:
        unittest.main()
