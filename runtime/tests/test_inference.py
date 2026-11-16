from contextlib import contextmanager
from contextlib import redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from community_grid import hardware, inference, worker


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
    def test_context_rejection_is_typed_without_exposing_backend_text(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"error":{"message":"prompt exceeds context size"}}'
                self.send_response(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.handle_request)
            thread.start()
            with self.assertRaises(inference.ModelContextExceeded):
                inference.completion(server.server_port, "short prompt")
            thread.join()

    def test_inference_child_returns_only_safe_context_error_code(self):
        output = io.StringIO()
        version = type("Version", (), {"stdout": inference.BACKEND_REVISION.encode()})()
        with patch("community_grid.worker.subprocess.run", return_value=version), \
             patch("community_grid.inference.run_threads",
                   side_effect=inference.ModelContextExceeded("private backend detail")), \
             redirect_stdout(output):
            worker.execute_child("backend", "model", 2, "prompt")
        self.assertEqual(json.loads(output.getvalue()), {"error_code": "model_context_exceeded"})

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
