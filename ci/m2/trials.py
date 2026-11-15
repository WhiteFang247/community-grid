"""Run the committed graph and failure matrix inside guest A."""
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from community_grid.enrollment import Registry
from community_grid.graph import digest, encode
from community_grid.native import Runtime, coordinator_owner, exchange
from community_grid.physical import ssh_transport

MANIFEST = Path("/etc/cgos/distributed/nodes.json")
FIXTURE = Path("/usr/share/cgos/m2-reference-graph.json")
A_SOCKET = "/run/cgos-coordinator/api.sock"
COUNTS = {"warmup": 5, "healthy": 30, "local": 30, "lost": 20, "restart": 20,
          "offline": 20, "cancel": 20}
EXPECTED = {"normalize": "seed library"}
EXPECTED["hash"] = hashlib.sha256(EXPECTED["normalize"].encode()).hexdigest()
EXPECTED["final"] = hashlib.sha256(EXPECTED["hash"].encode()).hexdigest()


def report(label, data):
    print(label + " " + json.dumps(data, sort_keys=True), flush=True)


def graph(identity, mode):
    doc = json.loads(FIXTURE.read_text())
    doc["id"] = identity
    if mode == "local":
        for item in doc["units"].values():
            item["nodes"] = ["local"]
    if mode == "offline":
        doc["units"]["final"]["nodes"] = ["A", "B"]
    return doc


def spin(action, limit):
    until = time.monotonic() + limit
    while time.monotonic() < until:
        if result := action():
            return result
        time.sleep(.07)
    raise RuntimeError("trial checkpoint did not occur within its deadline")


def verified(client, identity, node_check):
    values = {}
    status = client({"action": "graph-status", "id": identity})
    for name, value in EXPECTED.items():
        item = status[name]
        assert item["status"] == "completed", item
        reply = client({"action": "result", "id": identity, "unit": name})
        assert reply["value"] == value, (name, reply)
        assert reply["artifact"]["sha256"] == hashlib.sha256(json.dumps(value).encode()).hexdigest()
        if node_check:
            assert item["node"] == {"normalize": "A", "hash": "B", "final": "A"}[name]
        values[name] = reply
    return {"status": status, "results": values}


def ordinary(mode, identity):
    sock = "/run/cgos/api.sock" if mode == "local" else A_SOCKET
    def command(request):
        return exchange(sock, request)
    t0 = time.monotonic()
    command({"action": "submit", "graph": graph(identity, mode)})
    def ready():
        state = command({"action": "graph-status", "id": identity})["final"]["status"]
        if state in ("failed", "blocked", "cancelled", "timed-out"):
            raise RuntimeError(state)
        return state == "completed"
    spin(ready, 60)
    elapsed = time.monotonic() - t0
    return dict(seconds=elapsed, **verified(command, identity, mode != "local"))


class Barrier:
    def __init__(self, mode, number, path):
        self.mode, self.number, self.path = mode, number, path
        self.trigger = threading.Event()
        self.cut = threading.Event()
        self.target = None
        self.outcome = None
        self.metrics = []
        self.raw = {}
        self.marker = path / "barrier.json"

    def transport(self, address, user, identity, pin, port):
        wire = ssh_transport(address, user, identity, pin, port, enrolled=True)
        node = "A" if address.endswith(".1") else "B"
        self.raw[node] = wire

        def exchange_request(request):
            action, lease = request["action"], request.get("lease")
            if node == "B" and self.cut.is_set():
                raise ConnectionError("deterministic network partition")
            hit = (node == "B" and action == "submit" and not self.trigger.is_set()
                   and self.mode in ("lost", "restart", "offline", "cancel")
                   and lease["task"] == ("final" if self.mode == "offline" else "hash"))
            started = time.monotonic()
            if hit and self.mode == "cancel":
                self.target, self.outcome = lease, request
                self.trigger.set()
                raise ConnectionError("delayed before admission")
            try:
                reply = wire(request)
            finally:
                self.metrics.append({"node": node, "action": action,
                                     "seconds": time.monotonic() - started})
            if hit:
                assert reply["status"] in ("queued", "running", "completed")
                self.target = lease
                self.marker.write_text(json.dumps(dict(lease=lease, since=time.monotonic(),
                                                       rpc=self.metrics)))
                if self.mode == "offline":
                    self.cut.set()
                    print(f"M2_LINK_DOWN_{self.number}", flush=True)
                self.trigger.set()
                raise ConnectionError("worker response deliberately withheld after admission")
            return reply
        return exchange_request


def fault(mode, number, folder, identity, stage_only=False):
    barrier = Barrier(mode, number, folder)
    previous = json.loads(barrier.marker.read_text()) if barrier.marker.exists() else None
    if previous is not None:
        barrier.target = previous["lease"]
        barrier.trigger.set()
    registry = Registry(MANIFEST, folder, transport_factory=barrier.transport)
    try:
        with coordinator_owner(folder, True):
            rt = Runtime(folder, registry)
            def status():
                rt.tick()
                return rt.graphs.status(identity, "local")["units"]
            if previous is None:
                start = time.monotonic()
                rt.request({"action": "submit", "graph": graph(identity, mode)})
                (folder / "start").write_text(str(start))
            else:
                start = float((folder / "start").read_text())
            if mode == "offline":
                eligible = registry.peers["A"].eligible
                registry.peers["A"].eligible = lambda unit: (
                    (unit["op"] != "text.sha256" or barrier.trigger.is_set()) and eligible(unit))
            if previous is None:
                def reached():
                    status()
                    return barrier.trigger.is_set()
                spin(reached, 35)
            old = barrier.target
            if stage_only:
                assert old is not None
                return None
            task = old["task"]
            stopped = (json.loads(barrier.marker.read_text())["since"]
                       if barrier.marker.exists() else time.monotonic())
            offline_detect_s = None
            prior = None
            if mode == "offline":
                last_seen = registry.peers["B"].last_seen
                def offline():
                    status()
                    return not registry.peers["B"].online()
                spin(offline, 12)
                offline_detect_s = time.monotonic() - last_seen
            if mode == "cancel":
                rt.request({"action": "graph-cancel", "id": identity})
                spin(lambda: status()[task]["status"] == "cancelled"
                     and not quarantined(rt, old), 20)
                prior = barrier.raw["B"](barrier.outcome)
                assert prior["status"] == "cancelled" and "value" not in prior
                data = encode("incorrect")
                assert not rt.graphs.publish(identity, "local", task, old["attempt"], data,
                                              digest(data), lease=old, worker_id="B")
                state = status()
                assert state["final"]["status"] == "cancelled" and not quarantined(rt)
                return {"seconds": time.monotonic()-start, "recovery_s": time.monotonic()-stopped,
                        "attempts": state, "tombstone": prior["status"], "rpc": barrier.metrics}
            def complete():
                state = status()
                if state["final"]["status"] in ("failed", "blocked", "cancelled", "timed-out"):
                    raise RuntimeError(state)
                return state if state["final"]["status"] == "completed" else None
            state = spin(complete, 45)
            recovered = time.monotonic()
            if mode == "offline":
                history = state["final"]["history"]
                assert history[0]["outcome"] == "timed-out" and len(history) == 2
                assert state["final"]["lease"]["generation"] == 2 and quarantined(rt, old)
                assert state["hash"]["node"] == "B"
                print(f"M2_LINK_UP_{number}", flush=True)
                barrier.cut.clear()
                spin(lambda: status() and not quarantined(rt, old), 20)
                prior = barrier.raw["B"]({"version": 2, "action": "status", "lease": old})
                assert prior["status"] == "completed"
                assert not rt.graphs.publish(identity, "local", task, old["attempt"],
                                              encode(prior["value"]), prior["sha256"],
                                              lease=old, worker_id="B")
            else:
                assert state["hash"]["attempt"] == old["attempt"]
                assert state["hash"]["lease"]["generation"] == 1
                assert len(state["hash"]["history"]) == 1
                admission = barrier.raw["B"]({"version": 2, "action": "status", "lease": old})
                assert admission["status"] == "completed"
            assert not quarantined(rt)
            for item in state.values():
                assert sum(x.get("outcome") == "completed" for x in item["history"]) == 1
            return {"seconds": recovered-start, "recovery_s": recovered-stopped,
                    "offline_detect_s": offline_detect_s, "attempts": state,
                    "late_result": prior, "rpc": ((previous or {}).get("rpc", []) + barrier.metrics),
                    **verified(rt.request, identity, True)}
    finally:
        registry.close()


def quarantined(runtime, lease=None):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(runtime.path)) as db:
        if lease:
            return db.execute("SELECT 1 FROM lease_quarantine WHERE attempt=?", (lease["attempt"],)).fetchone()
        return db.execute("SELECT 1 FROM lease_quarantine LIMIT 1").fetchone()


def summary(values):
    ordered = sorted(values)
    return dict(n=len(ordered), median=statistics.median(ordered),
                p95=ordered[(95*len(ordered)+99)//100-1], maximum=ordered[-1])


def main():
    if len(sys.argv) > 1:
        fault("restart", int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], stage_only=True)
        return
    failures = []
    totals = {}
    for mode, count in COUNTS.items():
        measurements = []
        if mode == "lost":
            subprocess.run(["systemctl", "stop", "cgos-coordinator.service"], check=True)
        for i in range(1, count+1):
            identity = mode + "-" + uuid.uuid4().hex
            try:
                if mode in ("warmup", "healthy", "local"):
                    result = ordinary(mode, identity)
                else:
                    with tempfile.TemporaryDirectory(prefix="cgos-fault-", dir="/run") as d:
                        if mode == "restart":
                            subprocess.run([sys.executable, __file__, "stage", str(i), d, identity],
                                           check=True, timeout=45)
                        result = fault(mode, i, Path(d), identity)
                report("M2_TRIAL", dict(mode=mode, trial=i, id=identity, status="pass", **result))
                measurements.append(result["seconds"])
                if result.get("recovery_s", 0) > 45:
                    failures.append((mode,i,"recovery exceeded 45 seconds"))
                if (result.get("offline_detect_s") or 0) > 10:
                    failures.append((mode,i,"offline observation exceeded 10 seconds"))
                if any(x["seconds"] > 3 for x in result.get("rpc", [])):
                    failures.append((mode,i,"RPC exceeded 3 seconds"))
            except (Exception, AssertionError) as exc:
                report("M2_TRIAL", dict(mode=mode, trial=i, id=identity, status="fail", error=str(exc)))
                failures.append((mode,i,str(exc)))
                if mode == "offline":
                    print(f"M2_LINK_UP_{i}", flush=True)
                # A failed checkpoint may have left a worker occupied. Preserve
                # the trial and stop instead of hiding failures with retries.
                report("M2_VERDICT", dict(status="FAIL", failures=failures, summary=totals))
                raise
        totals[mode] = summary(measurements)
    if totals["healthy"]["p95"] > 20:
        failures.append(("healthy","p95","over 20 seconds"))
    report("M2_VERDICT", dict(status="FAIL" if failures else "PASS",
                             failures=failures, summary=totals))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
