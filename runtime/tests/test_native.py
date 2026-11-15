import hashlib
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from community_grid.native import Runtime, exchange
from test_graph import unit


class NativeTests(unittest.TestCase):
    def test_durable_local_dispatch_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            runtime = Runtime(state)
            graph = {"version": 1, "id": "recover", "owner": "local", "units": {
                "hash": unit("text.sha256", "local", value="seed library"),
            }}
            runtime.request({"action": "submit", "graph": graph})
            runtime.tick()
            runtime.tick()
            runtime = Runtime(state)
            runtime.tick()
            result = runtime.request({"action": "result", "id": "recover", "unit": "hash"})
            expected = hashlib.sha256(b"seed library").hexdigest()
            self.assertEqual(result["value"], expected)
            self.assertEqual(result["artifact"]["sha256"],
                             hashlib.sha256(json.dumps(expected).encode()).hexdigest())
            self.assertEqual(runtime.request({"action": "status"})["active_graphs"], 0)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Python lacks AF_UNIX")
    def test_console_graph_result_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint = str(root / "api.sock")
            command = [sys.executable, "-m", "community_grid.native", "--socket", endpoint]
            graph = {"version": 1, "id": "reference", "owner": "local", "units": {
                "normalize": unit("text.normalize", "local", value="  seed   library "),
                "hash": unit("text.sha256", "local", ("normalize",), **{"from": "normalize"}),
            }}
            fixture = root / "graph.json"
            fixture.write_text(json.dumps(graph))
            expected = hashlib.sha256(b"seed library").hexdigest()
            for first_boot in (True, False):
                daemon = subprocess.Popen(command + ["serve", "--state", str(root / "state")])
                try:
                    end = time.monotonic() + 5
                    while True:
                        try:
                            self.assertEqual(exchange(endpoint, {"action": "status"})["service"], "ready")
                            break
                        except OSError:
                            if daemon.poll() is not None or time.monotonic() >= end:
                                raise
                            time.sleep(0.05)
                    def cli(*args):
                        return json.loads(subprocess.check_output(command + list(args), timeout=5))

                    if first_boot:
                        self.assertEqual(cli("graph", "submit", str(fixture)), {"id": "reference"})
                        end = time.monotonic() + 5
                        while cli("graph", "status", "reference")["hash"]["status"] != "completed":
                            self.assertLess(time.monotonic(), end)
                    answer = cli("graph", "result", "reference", "hash")
                    self.assertEqual(answer["value"], expected)
                    self.assertEqual(answer["artifact"]["sha256"],
                                     hashlib.sha256(json.dumps(expected).encode()).hexdigest())
                finally:
                    if daemon.poll() is None:
                        daemon.send_signal(signal.SIGTERM)
                    daemon.wait(timeout=5)
                    if sys.platform == "win32":
                        Path(endpoint).unlink(missing_ok=True)
                    else:
                        self.assertEqual(daemon.returncode, 0)
                        self.assertFalse(Path(endpoint).exists())
