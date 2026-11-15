from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from community_grid.graph import GraphRuntime, digest
from community_grid.physical import PhysicalNode, process_transport
from test_graph import unit


class PhysicalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.nodes = {}
        self.transports = {}
        for name in ("lenovo", "dell"):
            argv = [sys.executable, "-m", "community_grid.physical", str(self.root / (name + ".db"))]
            self.transports[name] = process_transport(argv)
            self.nodes[name] = PhysicalNode(self.transports[name])
        self.graph = {"id": "test", "owner": "app", "version": 1, "units": {
            "normalize": unit("text.normalize", "lenovo", value="  seed\t library "),
            "hash": unit("text.sha256", "dell", ("normalize",), **{"from": "normalize"}),
        }}
        self.runtime = GraphRuntime(self.root / "graph.db", self.nodes)
        self.runtime.submit(self.graph, "app")

    def tick(self):
        self.runtime.tick("test", "app")

    def record(self, name):
        return self.runtime.status("test", "app")["units"][name]

    def test_lost_ack_restart_cross_node_and_uuid(self):
        exchange = self.transports["lenovo"]

        def lose(request):
            result = exchange(request)
            if request["action"] == "submit":
                raise ConnectionError("Lost after durable commit")
            return result

        self.nodes["lenovo"] = PhysicalNode(lose)
        self.tick()
        self.tick()
        attempt = self.record("normalize")["attempt"]
        self.nodes["lenovo"] = PhysicalNode(exchange)
        self.runtime = GraphRuntime(self.root / "graph.db", self.nodes)
        for _ in range(4):
            self.tick()
        self.assertEqual(self.runtime.result("test", "app", "hash"), digest(b"seed library"))
        self.assertEqual(self.record("normalize")["attempt"], attempt)
        self.assertTrue(self.record("normalize")["acknowledged"])
        self.assertIn("finished_at", self.record("hash"))
        with closing(sqlite3.connect(self.root / "lenovo.db")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone(), (1,))

    def test_correct_digest_of_incorrect_output_is_rejected(self):
        exchange = self.transports["lenovo"]

        def corrupt(request):
            result = exchange(request)
            if request["action"] == "status" and result["status"] == "completed":
                result.update(value="wrong", sha256=digest(b'"wrong"'))
            return result

        self.nodes["lenovo"] = PhysicalNode(corrupt)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.record("normalize")["status"], "failed")
        self.assertEqual(self.record("hash")["status"], "blocked")

    def test_expiration_while_disconnected_holds_slot_until_cleanup(self):
        now = [100.]
        runtime = GraphRuntime(self.root / "deadline.db", self.nodes, clock=lambda: now[0])
        runtime.submit(dict(self.graph, timeout=2), "app")
        runtime.tick("test", "app")
        del self.nodes["lenovo"]
        now[0] += 3
        runtime.tick("test", "app")
        record = runtime.status("test", "app")["units"]["normalize"]
        self.assertEqual(record["status"], "timed-out")
        self.assertTrue(record["cleanup_pending"])
        self.nodes["lenovo"] = PhysicalNode(self.transports["lenovo"])
        runtime.tick("test", "app")
        self.assertFalse(runtime.status("test", "app")["units"]["normalize"]["cleanup_pending"])

    def test_forced_entrypoint_rejects_command_and_oversized_input(self):
        argv = [sys.executable, "-m", "community_grid.physical", str(self.root / "forced.db")]
        with patch.dict(os.environ, SSH_ORIGINAL_COMMAND="sh"):
            self.assertEqual(subprocess.run(argv, input=b'{"action":"probe"}', capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run(argv, input=b" " * 65537, capture_output=True).returncode, 2)

    def test_response_and_request_size_bounds(self):
        exchange = process_transport([sys.executable, "-c", "print('x' * 1000000)"])
        with self.assertRaisesRegex(ValueError, "oversized relay response"):
            exchange({"action": "probe"})
        with self.assertRaisesRegex(ValueError, "Oversized relay request"):
            exchange({"value": "x" * 65536})

    def test_cancel_tombstone_rejects_late_dispatch(self):
        self.tick()
        record = self.record("normalize")
        attempt = record["attempt"]
        self.runtime.cancel("test", "app")
        self.tick()
        self.assertEqual(self.record("normalize")["status"], "cancelled")
        exchange = self.transports["lenovo"]
        late = {"action": "submit", "id": attempt, "op": "text.normalize",
                "value": "late", "cpu": 1, "ram_mib": 32, "deadline": record["deadline"]}
        self.assertEqual(exchange(late), {"id": attempt, "status": "cancelled"})
        self.assertFalse(self.runtime.publish("test", "app", "normalize", attempt, b'""', digest(b'""')))
