import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest

from community_grid.jobs import JobQueue
from community_grid.server import make_handler
from community_grid.worker import Worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "jobs.db")
        self.queue = JobQueue(self.path)

    def wait_for(self, job, expected):
        for _ in range(150):
            data = self.queue.get(job)
            if data["status"] == expected:
                return data
            time.sleep(0.02)
        self.fail(f"Expected {expected}, got {data['status']}")

    def start(self, execute):
        worker = Worker(self.queue, execute, 5)
        worker.start()
        self.addCleanup(worker.close)
        return worker

    def test_success_failure_timeout_and_persistence(self):
        def execute(prompt, cancelled, timeout):
            self.assertEqual(timeout, 5)
            if prompt == "failure":
                raise RuntimeError("backend exited")
            if prompt == "deadline":
                raise TimeoutError
            return {"text": prompt, "predicted_n": 1}

        self.start(execute)
        for prompt, state in (("ok", "completed"), ("failure", "failed"), ("deadline", "timed-out")):
            job = self.queue.submit_inference(prompt)
            self.wait_for(job, state)
            stored = JobQueue(self.path).get(job)
            self.assertEqual(stored["status"], state)
            self.assertTrue(stored["started_at"])
            self.assertTrue(stored["finished_at"])
        self.assertEqual(JobQueue(self.path).list_jobs(limit=3)[-1]["result"]["text"], "ok")

    def test_running_and_queued_cancellation(self):
        entered = Event()
        stopped = Event()

        def execute(prompt, cancelled, timeout):
            entered.set()
            while not cancelled():
                time.sleep(0.01)
            stopped.set()
            raise InterruptedError

        self.start(execute)
        running = self.queue.submit_inference("running")
        self.assertTrue(entered.wait(2))
        queued = self.queue.submit_inference("queued")
        self.assertTrue(self.queue.cancel_inference(queued))
        self.assertTrue(self.queue.cancel_inference(running))
        self.assertTrue(stopped.wait(2))
        self.assertFalse(self.queue.cancel_inference(running))
        self.assertEqual(self.queue.get(queued)["status"], "cancelled")

    def test_restart_recovers_interrupted_and_preserves_queued(self):
        running = self.queue.submit_inference("was running")
        self.assertEqual(self.queue.claim_inference()["id"], running)
        queued = self.queue.submit_inference("still queued")
        self.start(lambda prompt, *_: {"text": prompt})
        self.assertIn("interrupted", self.wait_for(running, "failed")["result"]["error"])
        self.wait_for(queued, "completed")

    def test_shutdown_keeps_queued_jobs_for_restart(self):
        entered = Event()

        def execute(prompt, cancelled, timeout):
            entered.set()
            while not cancelled():
                time.sleep(0.01)
            raise InterruptedError

        worker = self.start(execute)
        running = self.queue.submit_inference("running")
        self.assertTrue(entered.wait(2))
        queued = self.queue.submit_inference("queued")
        worker.close()
        self.assertEqual(self.queue.get(running)["status"], "cancelled")
        self.assertEqual(self.queue.get(queued)["status"], "queued")

    def test_loopback_api_authorization(self):
        token = b"a" * 32
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.queue, object(), token))
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

        def request(verb, path, data=None, authorized=False):
            headers = {"Content-Type": "application/json"}
            if authorized:
                headers["Authorization"] = "Bearer " + token.decode()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                conn.request(verb, path, json.dumps(data) if data is not None else None, headers)
                with conn.getresponse() as response:
                    return response.status, json.load(response)
            finally:
                conn.close()

        self.assertEqual(request("POST", "/api/jobs", {"prompt": "hello"})[0], 401)
        self.assertEqual(request("POST", "/api/jobs", {"prompt": "hi", "command": "ls"}, True)[0], 400)
        code, job = request("POST", "/api/jobs", {"prompt": "hello"}, True)
        self.assertEqual(code, 202)
        self.assertNotIn("payload", request("GET", "/api/jobs")[1][0])
        self.assertEqual(request("GET", "/api/jobs", authorized=True)[1][0]["payload"]["prompt"], "hello")
        self.assertEqual(request("POST", f"/api/jobs/{job['id']}/cancel", authorized=True)[0], 200)
