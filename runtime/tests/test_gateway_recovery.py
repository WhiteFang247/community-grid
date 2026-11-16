"""Recovery contracts without starting a model or touching a real SSH host."""

from collections import deque
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Lock
import unittest

from community_grid.inference import BACKEND_REVISION, MODEL_SHA256
from community_grid.openai_gateway import Gateway, GatewayError, transport_error


class Relay:
    def __init__(self):
        self.jobs = {}
        self.posts = 0
        self.online = True
        self.finish = True

    def __call__(self, method, path, body=None):
        if not self.online:
            raise OSError("temporary connection loss")
        if path == "/api/worker":
            return 200, {"healthy": True, "busy": False, "model_sha256": MODEL_SHA256,
                         "backend_revision": BACKEND_REVISION, "logical_cpus": 4,
                         "memory_available_bytes": 8 * 1024**3,
                         "limits": {"threads": 2, "max_running": 1}}
        job_id = path.rsplit("/", 1)[1]
        if method == "POST":
            self.posts += 1
            self.jobs[body["id"]] = body["prompt"]
            return 202, {"id": body["id"]}
        if job_id not in self.jobs:
            return 404, {"error": "absent"}
        if not self.finish:
            return 200, {"id": job_id, "status": "running", "result": None}
        return 200, {"id": job_id, "status": "completed", "result": {"samples": [
            {"text": "Four.", "prompt_n": 8, "predicted_n": 2}]}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.relay = Relay()
        self.gateway = Gateway.__new__(Gateway)
        self.gateway.name = "dell"
        self.gateway.transport = self.relay
        self.gateway.state = Path(self.temp.name) / "jobs.json"
        self.gateway.deadline_seconds = 1.0
        self.gateway.lock = Lock()
        self.gateway.failures = deque(maxlen=16)

    def test_health_separates_api_from_worker(self):
        self.assertEqual(self.gateway.health()["status"], "HEALTHY")
        self.relay.online = False
        health = self.gateway.health()
        self.assertEqual(health["status"], "DEGRADED")
        self.assertTrue(health["api"])
        self.assertFalse(health["worker"])
        self.assertEqual(health["reason"], "worker_unavailable")

    def test_simulated_network_loss_then_recovery(self):
        self.relay.online = False
        with self.assertRaises(GatewayError) as raised:
            self.gateway.complete("hello", request_key="c" * 64)
        self.assertEqual(raised.exception.code, "worker_unavailable")
        self.assertEqual(self.relay.posts, 0)
        self.relay.online = True
        job_id, result = self.gateway.complete("hello", request_key="c" * 64)
        self.assertEqual(result["choices"][0]["message"]["content"], "Four.")
        self.assertEqual(self.relay.posts, 1)
        self.assertEqual(self.gateway.job_status(job_id)["status"], "completed")

    def test_timeout_preserves_id_and_retry_does_not_duplicate(self):
        self.relay.finish = False
        with self.assertRaises(GatewayError) as raised:
            self.gateway.complete("hello", request_key="a" * 64)
        first = raised.exception
        self.assertEqual(first.status, 504)
        self.assertIsNotNone(first.job_id)
        self.assertEqual(self.relay.posts, 1)
        saved = json.loads(self.gateway.state.read_text())
        self.assertEqual(saved[first.job_id]["status"], "running")
        self.relay.finish = True
        job_id, result = self.gateway.complete("hello", request_key="a" * 64)
        self.assertEqual(job_id, first.job_id)
        self.assertEqual(result["choices"][0]["message"]["content"], "Four.")
        self.assertEqual(self.relay.posts, 1)
        self.gateway.complete("hello", request_key="a" * 64)
        self.assertEqual(self.relay.posts, 1)

    def test_unrelated_request_reconciles_before_new_dispatch(self):
        self.relay.finish = False
        with self.assertRaises(GatewayError) as raised:
            self.gateway.complete("first", request_key="a" * 64)
        first_id = raised.exception.job_id
        with self.assertRaises(GatewayError) as blocked:
            self.gateway.complete("second", request_key="b" * 64)
        self.assertEqual(blocked.exception.code, "reconciliation_pending")
        self.assertEqual(blocked.exception.job_id, first_id)
        self.assertEqual(self.relay.posts, 1)
        self.relay.finish = True
        second_id, _ = self.gateway.complete("second", request_key="b" * 64)
        self.assertNotEqual(second_id, first_id)
        self.assertEqual(self.relay.posts, 2)

    def test_background_reconciliation_preserves_uncertain_job(self):
        self.relay.finish = False
        with self.assertRaises(GatewayError) as raised:
            self.gateway.complete("hello", request_key="d" * 64)
        job_id = raised.exception.job_id
        self.relay.online = False
        self.assertEqual(self.gateway.reconcile_once(), "offline")
        self.relay.online = True
        self.relay.finish = True
        self.assertEqual(self.gateway.reconcile_once(), "resolved")
        self.assertEqual(self.gateway.job_status(job_id)["status"], "completed")
        self.assertEqual(self.relay.posts, 1)

    def test_host_key_and_public_key_failures_are_distinct(self):
        host = subprocess.CalledProcessError(255, "ssh", stderr="Host key verification failed")
        auth = subprocess.CalledProcessError(255, "ssh", stderr="Permission denied (publickey)")
        self.assertEqual(transport_error(host).code, "host_key_verification_failed")
        self.assertEqual(transport_error(auth).code, "ssh_authentication_failed")

    def test_worker_context_error_is_openai_compatible(self):
        row = {"id": "7bc45754-c4cb-4978-96ef-090ad1fc7b46", "status": "failed",
               "worker": "dell", "result": {"code": "model_context_exceeded"}}
        with self.assertRaises(GatewayError) as raised:
            self.gateway._terminal(row)
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(raised.exception.code, "model_context_exceeded")


if __name__ == "__main__":
    unittest.main()
