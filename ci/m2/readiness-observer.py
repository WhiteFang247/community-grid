"""Observe the pinned coordinator's own concurrent probes without replacing its SSH transport."""
import itertools
import json
from pathlib import Path
from queue import Queue
import subprocess
from threading import Thread, local
import time

from community_grid import enrollment, native, physical

TRACE = Path("/var/lib/cgos-coordinator/readiness.jsonl")
EVENTS = Queue(maxsize=1024)
CONTEXT = local()
SERIAL = itertools.count(1)
Popen = subprocess.Popen
accept_capability = enrollment.Peer._probe


def write_events():
    with TRACE.open("a", buffering=1) as output:
        while (record := EVENTS.get()) is not None:
            output.write(json.dumps(record, separators=(",", ":")) + "\n")


class ObservedPopen(Popen):
    def __init__(self, *args, **kwargs):
        row = getattr(CONTEXT, "row", None)
        if row is not None:
            row["spawn_started"] = time.monotonic()
        super().__init__(*args, **kwargs)
        if row is not None:
            CONTEXT.child = self
            row["spawn_returned"] = time.monotonic()


def observe(address, user, identity, pin, port):
    wire = physical.ssh_transport(address, user, identity, pin, port, enrolled=True)

    def exchange(request):
        if request["action"] != "probe":
            return wire(request)
        row = {"event": "probe", "id": next(SERIAL), "address": address,
               "started": time.monotonic(), "budget_seconds": 3}
        CONTEXT.row, CONTEXT.child = row, None
        try:
            result = wire(request)
            # The coordinator consumes this diagnostic ID before validating the
            # unchanged v2 payload. It is never sent over the wire.
            result["_readiness_id"] = row["id"]
            row["outcome"] = "response"
            return result
        except Exception as exc:
            cause = exc.__cause__
            if str(exc) == "Relay response timed out":
                outcome = "response-timeout"
            elif isinstance(cause, subprocess.TimeoutExpired):
                outcome = "process-exit-timeout"
            elif isinstance(cause, OSError):
                outcome = "spawn-or-io-error"
            elif isinstance(exc, ConnectionError):
                outcome = "connection-error"
            else:
                outcome = "invalid-response"
            row["outcome"] = outcome
            row["exception"] = type(exc).__name__
            raise
        finally:
            row["ended"] = time.monotonic()
            row["returncode"] = CONTEXT.child.returncode if CONTEXT.child is not None else None
            CONTEXT.row = CONTEXT.child = None
            EVENTS.put_nowait(row)
    return exchange


def record_acceptance(self, response):
    identity = response.pop("_readiness_id", None)
    accept_capability(self, response)
    EVENTS.put_nowait({"event": "accepted", "id": identity, "address": self.policy["address"],
                       "boot_id": self.boot_id, "at": time.monotonic()})


class ObservedRegistry(enrollment.Registry):
    def __init__(self, path, state):
        super().__init__(path, state, transport_factory=observe)


if __name__ == "__main__":
    writer = Thread(target=write_events, daemon=True)
    writer.start()
    subprocess.Popen = ObservedPopen
    enrollment.Registry = ObservedRegistry
    enrollment.Peer._probe = record_acceptance
    try:
        native.main()
    finally:
        EVENTS.put(None, timeout=2)
        writer.join(timeout=2)
