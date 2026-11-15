from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from community_grid import hardware, inference


@contextmanager
def completion_server(events, requests):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


class InferenceTests(unittest.TestCase):
    def test_cpu_model_is_distinct_from_architecture(self):
        with patch.object(Path, "read_text", return_value="processor: 0\nmodel name : Intel Core i5-2500\n"):
            self.assertEqual(hardware._cpu_model(), "Intel Core i5-2500")
        with patch.object(Path, "read_text", side_effect=OSError), \
             patch.object(hardware.platform, "processor", return_value=""), \
             patch.object(hardware.platform, "machine", return_value="x86_64"):
            self.assertEqual(hardware._cpu_model(), "x86_64")

    def test_disabled_before_launch(self):
        with patch("sys.stderr"), patch.object(inference.subprocess, "Popen") as launch:
            with self.assertRaises(SystemExit):
                inference.main(["--backend", "missing", "--backend-sha256", "a" * 64, "--model", "missing"])
            launch.assert_not_called()

    def test_digest_rejects_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.gguf"
            path.write_bytes(b"GGUF")
            expected = hashlib.sha256(b"GGUF").hexdigest()
            self.assertEqual(inference.verified_digest(path, expected), expected)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                inference.verified_digest(path, expected)

    def test_stream_metrics_over_loopback(self):
        requests = []
        events = [{"content": ""}, {"content": "Seed"}, {"stop": True, "timings": {
            "prompt_n": 29, "prompt_per_second": 35.5,
            "predicted_n": 64, "predicted_per_second": 12.3,
        }}]
        with completion_server(events, requests) as port:
            result = inference.completion(port)
        self.assertEqual(requests[0][0], "/completion")
        self.assertFalse(requests[0][1]["cache_prompt"])
        self.assertEqual(result["predicted_per_second"], 12.3)
        self.assertGreater(result["ttft_ms"], 0)
        self.assertGreaterEqual(result["elapsed_ms"], result["ttft_ms"])

    def test_incomplete_stream_is_failure(self):
        with completion_server([{"content": "partial"}], []) as port:
            with self.assertRaisesRegex(ValueError, "final timing"):
                inference.completion(port)
