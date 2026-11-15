"""Bounded, durable protocol for the experimental text worker."""
import argparse
from contextlib import closing
import ipaddress
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Thread
import time
import uuid

from .graph import OPERATIONS, digest, encode

OPS = ("text.normalize", "text.sha256")
LIMIT = 65536


def handle(path, request, *, ram_mib=128, clock=time.time, worker=None):
    if "version" in request:
        if worker is None:
            raise ValueError("Versioned worker protocol is disabled")
        return worker.request(request)
    if worker is not None:
        raise ValueError("Versioned worker protocol requires version 2")
    action = request.get("action")
    if action == "probe" and set(request) == {"action"}:
        return {"operations": OPS, "cpu": 1, "ram_mib": ram_mib}
    if action not in ("submit", "status", "cancel"):
        raise ValueError("Invalid action")
    identity = request["id"]
    if str(uuid.UUID(identity)) != identity:
        raise ValueError("Invalid attempt ID")
    fields = {"action", "id"}
    if action == "submit":
        fields |= {"op", "value", "deadline", "cpu", "ram_mib"}
    if set(request) != fields or len(encode(request)) > LIMIT:
        raise ValueError("Invalid request")
    with closing(sqlite3.connect(path, timeout=2)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, spec BLOB, answer BLOB)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT spec,answer FROM attempts WHERE id=?", (identity,)).fetchone()
        if row:
            if action == "submit" and row[0] is not None and row[0] != encode(request):
                raise ValueError("Attempt ID conflict")
            return json.loads(row[1])
        if action == "status":
            return {"id": identity, "status": "missing"}
        if action == "cancel":
            answer = {"id": identity, "status": "cancelled"}
        else:
            cpu, ram = request["cpu"], request["ram_mib"]
            deadline, value, operation = request["deadline"], request["value"], request["op"]
            if (type(cpu) is not int or cpu != 1 or type(ram) is not int or not 1 <= ram <= ram_mib
                    or operation not in OPS or type(value) is not str or len(encode(value)) > LIMIT - 1024
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise ValueError("Unsupported task")
            started = clock()
            if deadline > started + 305:
                raise ValueError("Deadline outside worker window")
            answer = {"id": identity, "status": "timed-out", "started_at": started}
            if started < deadline:
                result = OPERATIONS[operation][2](value)
                if clock() < deadline:
                    answer.update(status="completed", value=result, sha256=digest(encode(result)))
            answer["finished_at"] = clock()
        db.execute("INSERT INTO attempts VALUES (?,?,?)",
                   (identity, encode(request) if action == "submit" else None, encode(answer)))
        return answer


class PhysicalNode:
    def __init__(self, transport):
        self.transport = transport
        self.execution_times = {}

    def eligible(self, unit):
        try:
            report = self.transport({"action": "probe"})
            return (unit["op"] in report["operations"] and unit["cpu"] <= report["cpu"]
                    and unit["ram_mib"] <= report["ram_mib"])
        except (ConnectionError, ValueError, TypeError, KeyError):
            return False

    def _call(self, action, attempt, **fields):
        reply = self.transport(dict(action=action, id=attempt, **fields))
        if (not isinstance(reply, dict) or reply.get("id") != attempt
                or reply.get("status") not in ("missing", "completed", "failed", "cancelled", "timed-out")):
            raise ValueError("Invalid worker response")
        return reply

    def dispatch(self, attempt, unit, value, deadline):
        if self._call("submit", attempt, op=unit["op"], value=value, deadline=deadline,
                      cpu=unit["cpu"], ram_mib=unit["ram_mib"])["status"] == "missing":
            raise ValueError("Dispatch was not acknowledged")

    def status(self, attempt):
        reply = self._call("status", attempt)
        self.execution_times = {}
        if "started_at" in reply or "finished_at" in reply:
            start, finish = reply.get("started_at"), reply.get("finished_at")
            if not all(type(t) in (int, float) and math.isfinite(t) for t in (start, finish)) or finish < start:
                raise ValueError("Invalid worker timestamps")
            self.execution_times.update(started_at=start, finished_at=finish)
        if reply["status"] == "completed":
            if type(reply.get("value")) is not str or type(reply.get("sha256")) is not str:
                raise ValueError("Invalid result fields")
            return "completed", encode(reply["value"]), reply["sha256"]
        return reply["status"], None, None

    def cancel(self, attempt):
        self._call("cancel", attempt)


def process_transport(argv):
    def exchange(request):
        payload = encode(request)
        if len(payload) > LIMIT:
            raise ValueError("Oversized relay request")
        output = []
        try:
            with tempfile.TemporaryFile() as source:
                source.write(payload)
                source.seek(0)
                with subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL) as child:
                    reader = Thread(target=lambda: output.append(child.stdout.read(LIMIT + 1)))
                    reader.start()
                    reader.join(timeout=5)
                    if reader.is_alive():
                        child.kill()
                        reader.join()
                        raise ConnectionError("Relay response timed out")
                    if not output or len(output[0]) > LIMIT:
                        child.kill()
                        raise ValueError("Invalid or oversized relay response")
                    try:
                        code = child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConnectionError("Relay unavailable") from exc
        if code == 2:
            raise ValueError("Relay rejected request")
        if code:
            raise ConnectionError("Relay unavailable")
        return json.loads(output[0])
    return exchange


def ssh_transport(address, user, identity, known_hosts, port=22227):
    address = str(ipaddress.IPv4Address(address))
    if not user or not user.isascii() or not user.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid SSH account")
    pin = Path(known_hosts).resolve(strict=True).as_posix()
    return process_transport([
        "ssh", "-F", os.devnull, "-T", "-p", str(port), "-i", str(Path(identity).resolve(strict=True)),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=3",
        "-o", f'UserKnownHostsFile="{pin}"', "-o", f"GlobalKnownHostsFile={os.devnull}",
        f"{user}@{address}",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--coordinator")
    parser.add_argument("--node")
    parser.add_argument("--memory-mib", type=int, default=128)
    args = parser.parse_args()
    if (args.coordinator is None) != (args.node is None):
        parser.error("--coordinator and --node must be provided together")
    if os.environ.get("SSH_ORIGINAL_COMMAND"):
        return 2
    try:
        data = sys.stdin.buffer.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Request too large")
        request = json.loads(data)
        if not isinstance(request, dict):
            raise ValueError("Expected request object")
        worker = None
        if args.node is not None:
            from .worker_v2 import Worker
            worker = Worker(args.database, args.coordinator, args.node,
                            Path("/proc/sys/kernel/random/boot_id").read_text().strip(), args.memory_mib)
        reply = encode(handle(args.database, request, worker=worker))
        if len(reply) > LIMIT:
            raise ValueError("Response exceeds wire limit")
        sys.stdout.buffer.write(reply)
    except (KeyError, TypeError, ValueError, RecursionError):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
