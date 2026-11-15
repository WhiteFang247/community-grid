import json
from pathlib import Path
import tempfile
import time
from threading import Thread
from http.server import ThreadingHTTPServer
import unittest
import sys
from unittest.mock import patch

from community_grid.coordinator import Coordinator, local_transport, run_batch
from community_grid.grid_relay import request
from community_grid.jobs import JobQueue
from community_grid.server import make_handler
from community_grid.worker import Worker


class CoordinatorTests(unittest.TestCase):
    def test_relay_rejects_paths_methods_and_extra_fields_before_http(self):
        cases = [
            ("GET", "/api/worker?x=1", None),
            ("GET", "http://example.com/api/worker", None),
            ("POST", "/api/worker", {}),
            ("POST", "/api/jobs", {"id": "bad", "prompt": "hi", "command": "ls"}),
        ]
        with patch("community_grid.grid_relay.http.client.HTTPConnection") as connect:
            for method, path, body in cases:
                with self.subTest(path=path, method=method), self.assertRaises(ValueError):
                    request(8767, "test-token", method, path, body)
            connect.assert_not_called()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / "coordinator.json"
        self.coordinator = Coordinator(self.state)
        memory = (patch("community_grid.windows_job.memory_status", return_value=(16 * 1024**3, 8 * 1024**3))
                  if sys.platform == "win32" else
                  patch("community_grid.server._mem_available_bytes", return_value=8 * 1024**3))
        memory.start()
        self.addCleanup(memory.stop)

    def worker(self, name, execute=None):
        queue = JobQueue(str(self.root / f"{name}.sqlite3"))
        execute = execute or (lambda prompt, *_: {"samples": [{"text": prompt, "predicted_n": 1}]})
        worker = Worker(queue, execute, 5)
        token = name * 32
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            queue, worker, token.encode(), {"threads": 1, "max_running": 1}
        ))
        thread = Thread(target=server.serve_forever)
        worker.start()
        thread.start()
        self.addCleanup(worker.close)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return queue, local_transport(server.server_port, token), server.server_port

    def test_local_two_worker_simulation(self):
        _, a, port = self.worker("a")
        _, b, _ = self.worker("b")
        with self.assertRaises(PermissionError):
            self.coordinator.enroll("a", a)
        with self.assertRaises(PermissionError):
            self.coordinator.enroll("unknown", local_transport(port, "wrong"), approved=True)
        for name, transport in (("a", a), ("b", b)):
            self.coordinator.enroll(name, transport, approved=True)
        with self.assertRaises(PermissionError):
            self.coordinator.submit("hello")
        ids = {self.coordinator.submit(prompt, approved=True) for prompt in ("one", "two")}
        report = run_batch(self.coordinator, 5)
        self.assertEqual(report["completed"], 2)
        self.assertEqual(report["output_tokens"], 2)
        self.assertEqual({job["worker"] for job in report["jobs"]}, {"a", "b"})
        self.assertEqual({job["worker_record"]["id"] for job in report["jobs"]}, ids)

    def test_offline_restart_and_cancellation(self):
        def execute(prompt, cancelled, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancelled():
                    raise InterruptedError
                time.sleep(.02)
            raise TimeoutError

        queue, transport, _ = self.worker("a", execute)
        offline = False

        def flaky(method, path, body=None):
            if offline:
                raise OSError("disconnected")
            result = transport(method, path, body)
            if method == "POST" and path == "/api/jobs":
                raise OSError("acceptance reply lost")
            return result

        self.coordinator.enroll("a", flaky, approved=True)
        job = self.coordinator.submit("one", approved=True)
        self.coordinator.tick()
        self.assertIsNotNone(queue.get(job))
        offline = True
        self.coordinator.tick()
        self.assertFalse(self.coordinator.available("a"))
        offline = False
        restored = Coordinator(self.state)
        restored.enroll("a", transport, approved=True)
        restored.cancel(job, approved=True)
        report = run_batch(restored, 4)
        self.assertEqual(report["jobs"][0]["status"], "cancelled")
        self.assertEqual(len(queue.list_jobs()), 1)
        self.assertEqual(json.loads(self.state.read_text())[job]["status"], "cancelled")

    def test_expired_heartbeat_and_insufficient_ram_block_assignment(self):
        now = 0
        _, transport, _ = self.worker("a")
        coordinator = Coordinator(self.state, clock=lambda: now)
        coordinator.enroll("a", transport, approved=True)
        self.assertTrue(coordinator.available("a"))
        now = 13
        self.assertFalse(coordinator.available("a"))
        job = coordinator.submit("one", approved=True)
        memory = (patch("community_grid.windows_job.memory_status", return_value=(16 * 1024**3, 1024))
                  if sys.platform == "win32" else
                  patch("community_grid.server._mem_available_bytes", return_value=1024))
        with memory:
            coordinator.tick()
        self.assertNotIn("worker", coordinator.jobs[job])
