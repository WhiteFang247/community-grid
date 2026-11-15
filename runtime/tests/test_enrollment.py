import base64
from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest

from community_grid.enrollment import Peer, Registry, read_manifest
from community_grid.native import Handler, Runtime, exchange
from community_grid.physical import process_transport
from test_graph import unit


class Inline:
    def submit(self, callback, request):
        future = Future()
        try:
            future.set_result(callback(request))
        except Exception as exc:
            future.set_exception(exc)
        return future


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
        self.pin = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        dummy = self.root / "identity"
        dummy.touch()
        self.entry = dict(node="worker", address="127.0.0.1", port=22227, account="relay",
                          host_key="ssh-ed25519 " + base64.b64encode(blob).decode(),
                          fingerprint=self.pin, identity_file=str(dummy), protocols=[2],
                          operations={"text.normalize": 1}, cpu=1, memory_mib=64, revoked=False)
        self.manifest = self.root / "nodes.json"
        self.write_manifest(self.entry)
        self.now = 0
        self.responses = dict(version=2, node="worker", worker_boot_id="boot-a",
                              architecture="x86_64", backend="cpu", slots=16, cpu=32,
                              ram_mib=4096, operation_versions={"text.normalize": 1, "text.sha256": 1})

    def write_manifest(self, *entries):
        self.manifest.write_text(json.dumps(dict(version=1, workers=entries)), encoding="utf-8")
        self.manifest.chmod(0o600)

    def test_pinned_enrollment_and_policy(self):
        self.assertEqual(read_manifest(self.manifest)["worker"], self.entry)
        self.write_manifest(dict(self.entry, fingerprint="SHA256:incorrect"))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            read_manifest(self.manifest)
        self.write_manifest(dict(self.entry, protocols=[1]))
        with self.assertRaises(ValueError):
            read_manifest(self.manifest)
        self.write_manifest(dict(self.entry, revoked=True))
        self.assertEqual(read_manifest(self.manifest), {})

    def test_clamping_protocol_offline_and_boot_identity(self):
        responses = self.responses
        peer = Peer(self.entry, lambda _: responses, Inline(), lambda: self.now)
        peer.poll()
        peer.poll()
        self.assertTrue(peer.online())
        self.assertEqual(peer.capabilities["slots"], 1)
        self.assertEqual(peer.capabilities["cpu"], 1)
        self.assertEqual(peer.capabilities["ram_mib"], 64)
        self.assertEqual(peer.capabilities["operation_versions"], {"text.normalize": 1})
        self.now = 2
        responses = dict(responses, worker_boot_id="boot-b", operation_versions={"text.normalize": 2})
        peer.reply = ({"attempt": "old"}, {})
        peer._probe(responses)
        self.assertIsNone(peer.reply)
        self.assertEqual(peer.boot_id, "boot-b")
        self.assertFalse(peer.eligible({"op": "text.normalize", "cpu": 1, "ram_mib": 32}))
        self.now = 12
        self.assertFalse(peer.online())
        responses = dict(responses, version=1)
        peer.poll()
        peer.poll()
        self.assertIsNone(peer.capabilities)

    def test_revocation_fences_active_lease_and_rejects_unknown_work(self):
        factory = lambda *args: lambda _: self.responses
        registry = Registry(self.manifest, self.root, transport_factory=factory, clock=lambda: self.now)
        self.addCleanup(registry.close)
        runtime = Runtime(self.root, registry, boot_id="coordinator")
        item = unit("text.normalize", "worker", value="seed")
        item["ram_mib"] = 32
        graph = dict(id="g", version=1, owner="local", units={"normalize": item})
        with self.assertRaises(ValueError):
            runtime.request({"action": "submit", "graph": dict(graph, units={
                "normalize": dict(item, nodes=["unknown"])})})
        runtime.request({"action": "submit", "graph": graph})
        end = time.monotonic() + 2
        while time.monotonic() < end:
            runtime.tick()
            record = runtime.graphs.status("g", "local")["units"]["normalize"]
            if record["status"] == "assigned":
                break
            time.sleep(.01)
        self.assertEqual(record["status"], "assigned")
        self.write_manifest(dict(self.entry, revoked=True))
        self.assertEqual(runtime.request({"action": "reload-nodes"}), {})
        self.assertEqual(runtime.graphs.status("g", "local")["units"]["normalize"]["status"], "waiting")
        self.write_manifest(self.entry)
        runtime.request({"action": "reload-nodes"})
        self.assertFalse(runtime.graphs.publish("g", "local", "normalize",
                                               record["attempt"], b'"seed"', "unused",
                                               lease=record["lease"], worker_id="worker"))

    def test_relay_timeout_is_bounded(self):
        transport = process_transport([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.25)
        started = time.monotonic()
        with self.assertRaises(ConnectionError):
            transport({"action": "probe"})
        self.assertLess(time.monotonic() - started, 2)

    @unittest.skipUnless(hasattr(socketserver, "UnixStreamServer") and hasattr(socket, "AF_UNIX"),
                         "Unix sockets unavailable")
    def test_unresponsive_probe_does_not_block_unix_api(self):
        gate = Event()
        began = Event()
        def slow(_):
            began.set()
            gate.wait(4)
            raise ConnectionError
        registry = Registry(self.manifest, self.root, transport_factory=lambda *args: slow)
        self.addCleanup(registry.close)
        runtime = Runtime(self.root, registry, boot_id="coordinator")
        with socketserver.UnixStreamServer(str(self.root / "api.sock"), Handler) as server:
            server.runtime = runtime
            server.timeout = 2
            runtime.tick()
            self.assertTrue(began.wait(1))
            thread = Thread(target=server.handle_request)
            thread.start()
            try:
                started = time.monotonic()
                self.assertEqual(exchange(str(self.root / "api.sock"), {"action": "status"})["service"], "ready")
                self.assertLess(time.monotonic() - started, 1)
            finally:
                gate.set()
                thread.join(3)
