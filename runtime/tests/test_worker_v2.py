import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
import uuid

from community_grid.graph import GraphRuntime, digest, encode
from community_grid.physical import handle
from community_grid.worker_v2 import Worker
from test_graph import unit


class WorkerFixture:
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "worker.db"
        self.worker = self.open()
        self.value = "  seed   library "
        self.lease = self.make_lease()

    def open(self):
        return Worker(self.path, "coordinator", "node", "worker-boot")

    def make_lease(self, **fields):
        lease = dict(graph="g", task="normalize", node="node", worker_boot_id="worker-boot",
                     coordinator_boot_id="coordinator-boot", generation=1, attempt=str(uuid.uuid4()),
                     deadline=100.0, budget_seconds=4, cpu=1, ram_mib=128,
                     operation="text.normalize", input_digest=digest(encode(self.value)))
        lease.update(fields)
        return dict(lease, request_digest=digest(encode(lease)))

    def request(self, action, lease=None, **fields):
        return handle(self.path, {"version": 2, "action": action,
                                 "lease": self.lease if lease is None else lease, **fields},
                      worker=self.worker)

class WorkerTests(WorkerFixture, unittest.TestCase):
    def test_cancel_before_admission_persists_and_blocks_delayed_submit(self):
        cancel = self.request("cancel")
        self.assertEqual(cancel["status"], "cancelled")
        self.worker = self.open()
        self.assertEqual(self.request("submit", value=self.value), cancel)
        self.assertEqual(self.request("status"), cancel)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.request("submit", self.make_lease(attempt=self.lease["attempt"], generation=2),
                         value=self.value)

    def test_duplicate_lost_ack_and_digest_checks(self):
        self.request("submit", value=self.value)  # Discard the acknowledgment.
        self.worker = self.open()
        admitted = self.request("status")
        self.assertEqual(admitted["status"], "queued")
        self.assertEqual(self.request("submit", value=self.value), admitted)
        with self.assertRaisesRegex(ValueError, "canonical request digest"):
            self.request("submit", dict(self.lease, request_digest="0"*64), value=self.value)
        with self.assertRaisesRegex(ValueError, "Input digest"):
            self.request("submit", value="other payload")
        with self.assertRaisesRegex(ValueError, "version"):
            handle(self.path, {"version": 1, "action": "probe"}, worker=self.worker)
        with self.assertRaisesRegex(ValueError, "version"):
            handle(self.path, {"action": "probe"}, worker=self.worker)
        self.assertEqual(handle(self.path, {"action": "probe"})["cpu"], 1)


@unittest.skipUnless(sys.platform == "linux", "Linux executor controls")
class ExecutorTests(WorkerFixture, unittest.TestCase):
    def launch(self, script, lease):
        marker = self.root / "pid"
        code = ("import os,sys,time\n"
                "from pathlib import Path\n"
                "from community_grid.worker_v2 import child, OPERATIONS\n"
                f"marker = Path({str(marker)!r})\n"
                "def operation(value):\n"
                "    marker.write_text(str(os.getpid()))\n"
                + script +
                "\nOPERATIONS['text.normalize'] = (str, str, operation)\n"
                "sys.exit(child('text.normalize', int(sys.argv[1]), int(sys.argv[2])))\n")
        return [sys.executable, "-c", code, str(lease["ram_mib"]), str(os.getpid())]

    def test_completed_is_replayed_after_restart_and_cancel(self):
        self.request("submit", value=self.value)
        with self.worker.executor() as lock:
            self.assertTrue(self.worker.run_one(lock))
            self.assertFalse(self.worker.run_one(lock))
        original = self.request("status")
        self.assertEqual(original["status"], "completed")
        self.assertEqual(original["value"], "seed library")
        self.assertGreater(original["child_peak_rss_kib"], 0)
        self.assertGreater(original["service_peak_rss_kib"], 0)
        self.worker = self.open()
        with self.worker.executor() as lock:
            self.assertEqual(self.request("submit", value=self.value), original)
            self.assertFalse(self.worker.run_one(lock))
        self.assertEqual(self.request("cancel"), original)

    def test_running_cancel_kills_and_reaps_child(self):
        self.worker.child_command = lambda lease: self.launch("    time.sleep(10)", lease)
        self.request("submit", value=self.value)
        with self.worker.executor() as lock:
            with self.assertRaises(BlockingIOError):
                with self.open().executor():
                    pass
            def run():
                try:
                    self.worker.run_one(lock)
                except Exception as exc:
                    errors.append(exc)
            errors = []
            thread = Thread(target=run)
            thread.start()
            try:
                marker = self.root / "pid"
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                pid = int(marker.read_text())
                self.assertEqual(self.request("cancel")["status"], "running")
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertEqual(self.request("status")["status"], "cancelled")
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_timeout_kills_and_reaps_child(self):
        self.lease = self.make_lease(budget_seconds=.7)
        self.worker.child_command = lambda lease: self.launch("    time.sleep(10)", lease)
        self.request("submit", value=self.value)
        with self.worker.executor() as lock:
            self.assertTrue(self.worker.run_one(lock))
        pid = int((self.root / "pid").read_text())
        self.assertFalse(Path(f"/proc/{pid}").exists())
        self.assertEqual(self.request("status")["status"], "timed-out")

    def test_memory_breach_is_enforced(self):
        self.lease = self.make_lease(ram_mib=64)
        self.worker.child_command = lambda lease: self.launch("    return bytearray(96 * 1024 * 1024)", lease)
        self.request("submit", value=self.value)
        with self.worker.executor() as lock:
            self.worker.run_one(lock)
        result = self.request("status")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "memory limit exceeded")
        self.assertFalse(Path(f"/proc/{int((self.root / 'pid').read_text())}").exists())

    def test_claimed_attempt_is_not_reexecuted_on_takeover(self):
        self.request("submit", value=self.value)
        with self.worker.transaction() as db:
            db.execute("UPDATE worker_v2_attempts SET state='running'")
        self.worker = self.open()
        with self.worker.executor() as lock:
            self.assertFalse(self.worker.run_one(lock))
        self.assertEqual(self.request("status")["status"], "failed")
        self.assertEqual(self.request("submit", value=self.value)["status"], "failed")

    def test_completed_response_cannot_bypass_coordinator_fence(self):
        class Adapter:
            boot_id = "worker-boot"
            def eligible(self, task):
                return True
            def lease_dispatch(peer, lease, value):
                self.request("submit", lease, value=value)
            def lease_status(peer, lease):
                reply = self.request("status", lease)
                if "value" in reply:
                    reply["data"] = encode(reply["value"])
                return reply
            def lease_cancel(peer, lease):
                self.request("cancel", lease)
        now = [10.0]
        runtime = GraphRuntime(self.root / "graph.db", {"node": Adapter()}, lambda: now[0],
                               coordinator_boot_id="coordinator-boot")
        runtime.submit({"id": "g", "owner": "test", "version": 1,
                        "units": {"normalize": unit("text.normalize", "node", value=self.value)}}, "test")
        runtime.tick("g", "test")
        runtime.tick("g", "test")
        lease = runtime.status("g", "test")["units"]["normalize"]["lease"]
        with self.worker.executor() as lock:
            self.worker.run_one(lock)
        reply = self.request("status", lease)
        self.assertEqual(reply["status"], "completed")
        now[0] = lease["deadline"]
        self.assertFalse(runtime.publish("g", "test", "normalize", lease["attempt"],
                                         encode(reply["value"]), reply["sha256"],
                                         lease=lease, worker_id="node"))
        with self.assertRaises(ValueError):
            runtime.result("g", "test", "normalize")
