"""Explicit SSH worker enrollment and nonblocking coordinator adapters."""
import base64
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .graph import digest, encode
from .physical import ssh_transport

OPS = {"text.normalize": 1, "text.sha256": 1}
MAX_PEERS = 8
PROBE_INTERVAL = 2
OFFLINE_AFTER = 9.5  # Leave headroom for observing the 10-second gate.


def read_manifest(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Enrollment manifest must be a regular file")
        if os.name == "posix" and (info.st_mode & 0o022 or info.st_uid not in (0, os.geteuid())):
            raise PermissionError("Enrollment manifest must be controlled by the administrator")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Enrollment manifest exceeds limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"version", "workers"} or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported enrollment format")
    workers = data["workers"]
    if not isinstance(workers, list) or len(workers) > MAX_PEERS:
        raise ValueError("Too many enrolled workers")
    enrolled, seen, endpoints = {}, set(), set()
    for entry in workers:
        if not isinstance(entry, dict) or set(entry) != {
            "node", "address", "port", "account", "host_key", "fingerprint",
            "identity_file", "protocols", "operations", "cpu", "memory_mib", "revoked",
        }:
            raise ValueError("Invalid enrollment fields")
        node = entry["node"]
        if not isinstance(node, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", node) or node in seen:
            raise ValueError("Invalid or duplicate worker ID")
        seen.add(node)
        if type(entry["revoked"]) is not bool:
            raise ValueError("Invalid revocation flag")
        if entry["revoked"]:
            continue
        if not isinstance(entry["address"], str):
            raise ValueError("Invalid worker address")
        address = str(ipaddress.IPv4Address(entry["address"]))
        port, account = entry["port"], entry["account"]
        if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(account, str)
                or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,31}", account)):
            raise ValueError("Invalid SSH endpoint")
        if (address, port) in endpoints:
            raise ValueError("Enrolled SSH endpoints must be unique")
        endpoints.add((address, port))
        key = entry["host_key"]
        if not isinstance(key, str) or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", key):
            raise ValueError("Expected an Ed25519 public key without comment")
        binary = base64.b64decode(key.split()[1], validate=True)
        if not binary.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20") or len(binary) != 51:
            raise ValueError("Invalid Ed25519 public key")
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(binary).digest()).decode().rstrip("=")
        if entry["fingerprint"] != fingerprint:
            raise ValueError("Independently recorded host-key fingerprint does not match")
        if not isinstance(entry["identity_file"], str) or any(c in entry["identity_file"] for c in "\r\n"):
            raise ValueError("Invalid identity path")
        identity = Path(entry["identity_file"])
        if not identity.is_absolute() or not identity.is_file():
            raise ValueError("Expected existing absolute SSH identity path")
        if (entry["protocols"] != [2] or type(entry["protocols"][0]) is not int
                or type(entry["cpu"]) is not int or entry["cpu"] != 1
                or type(entry["memory_mib"]) is not int or not 16 <= entry["memory_mib"] <= 128):
            raise ValueError("Invalid protocol or resource policy")
        ops = entry["operations"]
        if not isinstance(ops, dict) or not ops or any(
            k not in OPS or type(v) is not int or v != OPS[k] for k, v in ops.items()
        ):
            raise ValueError("Invalid operation policy")
        enrolled[node] = dict(entry, address=address)
    return enrolled


class Peer:
    """Scheduler-facing view. Only its executor thread performs SSH."""

    def __init__(self, entry, transport, pool, clock=time.monotonic):
        self.policy, self.transport, self.pool, self.clock = entry, transport, pool, clock
        self.capabilities = None
        self.boot_id = None
        self.last_seen = None
        self.next_probe = 0
        self.pending = None
        self.reply = None

    def online(self):
        return (self.capabilities is not None and self.last_seen is not None
                and self.clock() - self.last_seen < OFFLINE_AFTER)

    def _send(self, action, lease=None, **fields):
        request = dict(version=2, action=action, **fields)
        if lease is not None:
            request["lease"] = lease
        self.pending = (action, lease, self.pool.submit(self.transport, request))

    def _probe(self, response):
        if (response.get("node") != self.policy["node"]
                or not isinstance(response.get("worker_boot_id"), str)
                or not response["worker_boot_id"] or len(response["worker_boot_id"]) > 256
                or not isinstance(response.get("architecture"), str)
                or not response["architecture"] or len(response["architecture"]) > 64
                or response.get("backend") != "cpu"
                or not isinstance(response.get("operation_versions"), dict)):
            raise ValueError("Unexpected worker capability identity")
        for key in ("slots", "cpu", "ram_mib"):
            if type(response.get(key)) is not int or response[key] <= 0:
                raise ValueError("Invalid worker capability limits")
        ops = {k: v for k, v in self.policy["operations"].items()
               if type(response["operation_versions"].get(k)) is int
               and response["operation_versions"][k] == v}
        boot = response["worker_boot_id"]
        if self.boot_id != boot:
            self.reply = None
        self.boot_id = boot
        self.capabilities = dict(architecture=response["architecture"], backend="cpu", slots=1, cpu=1,
                                 ram_mib=min(response["ram_mib"], self.policy["memory_mib"]),
                                 operation_versions=ops)
        self.last_seen = self.clock()

    def poll(self):
        """Harvest one finished exchange; do not take the slot from lease work."""
        if self.pending is not None and self.pending[2].done():
            action, lease, future = self.pending
            self.pending = None
            try:
                response = future.result()
                if not isinstance(response, dict) or type(response.get("version")) is not int or response["version"] != 2:
                    raise ValueError("Incompatible worker protocol")
                if action == "probe":
                    self._probe(response)
                elif encode(response.get("lease")) != encode(lease):
                    raise ValueError("Invalid worker lease response")
                elif response.get("status") not in {
                    "missing", "queued", "running", "completed", "failed", "cancelled", "timed-out"
                }:
                    raise ValueError("Invalid worker status")
                elif action in ("submit", "cancel") and response["status"] == "missing":
                    raise ValueError("Worker did not acknowledge request")
                else:
                    if response["status"] == "completed":
                        if type(response.get("value")) is not str or not isinstance(response.get("sha256"), str):
                            raise ValueError("Invalid worker result")
                        response = dict(response, data=encode(response["value"]))
                    self.reply = (lease, response)
                    # Worker v2 validates the complete lease against its live
                    # boot ID before answering. Errors do not refresh liveness.
                    self.last_seen = self.clock()
            except ConnectionError:
                if lease is not None:
                    self.reply = (lease, ConnectionError("Worker exchange failed"))
            except (ValueError, TypeError, KeyError):
                if action == "probe":
                    self.capabilities = None
                else:
                    self.reply = (lease, ValueError("Worker rejected or returned an invalid response"))
            if action == "probe":
                self.next_probe = self.clock() + PROBE_INTERVAL

    def refresh(self, *, leased=False):
        # An online peer with an assigned lease gets the slot for lease RPCs.
        # On loss of liveness, a probe must recover boot identity first.
        if leased and self.online():
            return
        if self.pending is None and self.clock() >= self.next_probe and (self.reply is None or not self.online()):
            if not self.online():
                self.reply = None
            self._send("probe")

    def eligible(self, unit):
        return (self.online() and unit["op"] in self.capabilities["operation_versions"]
                and unit["cpu"] <= self.capabilities["cpu"] and unit["ram_mib"] <= self.capabilities["ram_mib"])

    def lease_status(self, lease):
        if not self.online() or self.boot_id != lease["worker_boot_id"]:
            raise ConnectionError("Worker offline or boot identity changed")
        if self.reply is not None and self.reply[0] == lease:
            _, response = self.reply
            self.reply = None
            if isinstance(response, Exception):
                raise response
            return response
        self._request("status", lease)

    def _request(self, action, lease, **fields):
        if not self.online() or self.boot_id != lease["worker_boot_id"] or self.pending is not None:
            raise ConnectionError("Worker unavailable or exchange pending")
        if self.reply is not None:
            self.reply = None
        self._send(action, lease, **fields)
        raise ConnectionError("Worker exchange pending")

    def lease_dispatch(self, lease, value):
        if not self.online():
            raise ConnectionError("Worker offline")
        unit = {"op": lease["operation"], "cpu": lease["cpu"], "ram_mib": lease["ram_mib"]}
        if not self.eligible(unit):
            raise ValueError("Lease exceeds enrollment policy")
        self._request("submit", lease, value=value)

    def lease_cancel(self, lease):
        # Let the scheduler consume the asynchronous cancel response as status.
        if self.reply is not None and self.reply[0] == lease:
            return
        self._request("cancel", lease)

    def describe(self):
        return {"boot_id": self.boot_id, "online": self.online(), "capabilities": self.capabilities}


class Registry:
    def __init__(self, path, state, *, transport_factory=partial(ssh_transport, enrolled=True), clock=time.monotonic):
        self.path, self.state, self.clock = path, state, clock
        self.factory = transport_factory
        self.peers = {}
        self.pool = ThreadPoolExecutor(max_workers=MAX_PEERS)
        self.pin_directory = tempfile.TemporaryDirectory(prefix="worker-pins-", dir=state)
        self.retired = {}

    def load(self, graphs):
        entries = read_manifest(self.path)
        self.apply(entries, graphs)

    def apply(self, entries, graphs):
        # Persist revocations before allowing any reply from the replacement map.
        for node in list(self.retired):
            if self.retired[node].done():
                self.retired.pop(node)
        replacing = {node: peer.pending[2] for node, peer in self.peers.items()
                     if entries.get(node) != peer.policy and peer.pending is not None
                     and not peer.pending[2].done()}
        if len(self.retired.keys() | replacing.keys()) > MAX_PEERS:
            raise ValueError("Too many retired worker exchanges; retry reload after they finish")
        new = {}
        for node, entry in entries.items():
            old = self.peers.get(node)
            if old is not None and old.policy == entry:
                new[node] = old
                continue
            endpoint = f'[{entry["address"]}]:{entry["port"]}'
            # OpenSSH uses the bare address on the default port.
            if entry["port"] == 22:
                endpoint = entry["address"]
            pin = Path(self.pin_directory.name) / f"{node}-{digest(encode(entry))}"
            pin.write_text(f'{endpoint} {entry["host_key"]}\n', encoding="ascii")
            transport = self.factory(entry["address"], entry["account"], entry["identity_file"], pin, entry["port"])
            new[node] = Peer(entry, transport, self.pool, self.clock)
        graphs.set_enrollment({node: digest(encode(entry)) for node, entry in entries.items()})
        self.retired.update(replacing)
        self.peers.clear()
        self.peers.update(new)

    def tick(self):
        for node in list(self.retired):
            if self.retired[node].done():
                self.retired.pop(node)
        for node, peer in self.peers.items():
            if node not in self.retired:
                peer.poll()

    def refresh(self, leased):
        for node, peer in self.peers.items():
            if node not in self.retired:
                peer.refresh(leased=node in leased)

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.pin_directory.cleanup()
