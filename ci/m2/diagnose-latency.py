"""Trace one reference graph against the existing disposable VM workers."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import time

from community_grid.enrollment import Registry
from community_grid.native import Runtime, coordinator_owner
from community_grid.physical import ssh_transport
from trials import MANIFEST, graph, report, spin, verified


def traced_transport(address, user, identity, pin, port):
    exchange = ssh_transport(address, user, identity, pin, port, enrolled=True)

    def traced(request):
        lease = request.get("lease") or {}
        started = time.monotonic()
        record = {"address": address, "action": request["action"], "started": started,
                  **{key: lease[key] for key in ("graph", "task", "attempt", "generation") if key in lease}}
        try:
            reply = exchange(request)
            record.update({key: reply[key] for key in ("status", "started_at", "finished_at") if key in reply})
            return reply
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["seconds"] = time.monotonic() - started
            report("M2_DIAG_RPC", record)
    return traced


def diagnose(runtime, identity):
    started = time.monotonic()
    document = graph(identity, "warmup")
    assert document["timeout"] == 60
    runtime.request({"action": "submit", "graph": document})
    report("M2_DIAG_SUBMIT", {"graph": identity, "started": started, "admitted": time.monotonic()})
    snapshots = None
    ready = set()

    def observe():
        nonlocal snapshots
        runtime.tick()
        units = runtime.graphs.status(identity, "local")["units"]
        observed = time.monotonic()
        for name, task in document["units"].items():
            if name not in ready and all(units[dep]["status"] == "completed" for dep in task["depends"]):
                report("M2_DIAG_READY", {"task": name, "observed": observed})
                ready.add(name)
        snapshot = {name: {key: unit[key] for key in (
            "status", "node", "attempt", "reason", "history", "acknowledged_at",
            "started_at", "finished_at", "deadline") if key in unit} for name, unit in units.items()}
        if snapshot != snapshots:
            report("M2_DIAG_STATE", {"seconds": time.monotonic() - started, "units": snapshot})
            snapshots = snapshot
        return units["final"]["status"] in {"completed", "failed", "blocked", "cancelled", "timed-out"}

    try:
        spin(observe, 60)
        report("M2_DIAG_VERIFIED", verified(runtime.request, identity, True))
    finally:
        with closing(sqlite3.connect(runtime.path.as_uri() + "?mode=ro", uri=True)) as db:
            quarantine = [json.loads(row[0]) for row in db.execute("SELECT lease FROM lease_quarantine")]
        units = runtime.graphs.status(identity, "local")["units"]
        report("M2_DIAG_END", {"seconds": time.monotonic() - started, "units": snapshots,
                               "reservations": {name: unit["attempt"] for name, unit in units.items()
                                                if unit["status"] == "assigned" or unit.get("cleanup_pending")},
                               "quarantine": quarantine})


if __name__ == "__main__":
    import uuid

    with tempfile.TemporaryDirectory(prefix="cgos-latency-", dir=Path("/run")) as path:
        state = Path(path)
        registry = Registry(MANIFEST, state, transport_factory=traced_transport)
        try:
            with coordinator_owner(state, True):
                diagnose(Runtime(state, registry), "diag-" + uuid.uuid4().hex)
        finally:
            registry.close()
