"""Disposable guest experiment; the stock and observed services are compared unchanged."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from community_grid.native import exchange
from trials import A_SOCKET, graph, report, verified

UNIT = "cgos-coordinator.service"
STATE = Path("/var/lib/cgos-coordinator")
DROP = Path("/run/systemd/system") / (UNIT + ".d") / "acceptance.conf"
PROPERTIES = ("User", "Group", "ExecStart", "Environment", "WorkingDirectory", "MainPID",
              "ControlGroup", "CPUQuotaPerSecUSec", "CPUUsageNSec", "MemoryMax", "MemoryPeak", "TasksMax")


def run(*argv):
    subprocess.run(argv, check=True, timeout=25)


def command(request):
    return exchange(A_SOCKET, request)


def sample():
    run("systemctl", "show", UNIT, *("-p" + name for name in PROPERTIES))
    pid = subprocess.check_output(["systemctl", "show", UNIT, "-p", "MainPID", "--value"], text=True).strip()
    pid = Path("/proc") / str(int(pid))
    report("M2_PROC", {"cwd": os.readlink(pid / "cwd"), "io": (pid / "io").read_text(),
                       "load": Path("/proc/loadavg").read_text().strip(),
                       "state_uid": STATE.stat().st_uid, "state_mode": oct(STATE.stat().st_mode & 0o777)})
    cgroup = subprocess.check_output(["systemctl", "show", UNIT, "-p", "ControlGroup", "--value"], text=True).strip()
    report("M2_CGROUP_CPU", {"stat": (Path("/sys/fs/cgroup") / cgroup.lstrip("/") / "cpu.stat").read_text()})


def condition(label, observed):
    run("systemctl", "stop", UNIT)
    if STATE.exists():
        STATE.rename(STATE.with_name(STATE.name + "-before-" + label))
    DROP.unlink(missing_ok=True)
    if observed:
        DROP.parent.mkdir(parents=True, exist_ok=True)
        DROP.write_text("[Service]\nExecStart=\nExecStart=/usr/bin/python3 /run/cgos-acceptance-observe.py "
                        "--socket /run/cgos-coordinator/api.sock serve --state /var/lib/cgos-coordinator "
                        "--enrollment /etc/cgos/distributed/nodes.json\n")
    run("systemctl", "daemon-reload")
    started = time.monotonic()
    report("M2_AB_START", {"condition": label, "started": started})
    run("systemctl", "start", UNIT)
    previous, current = None, {}
    identity, result = None, "not-eligible"
    try:
        while time.monotonic() - started < 20:
            try:
                current = command({"action": "nodes"})
            except (OSError, ValueError):
                time.sleep(.2)
                continue
            if current != previous:
                report("M2_AB_NODES", {"condition": label, "seconds": time.monotonic()-started, "nodes": current})
                previous = current
            time.sleep(.24)
        if set(current) == {"A", "B"} and all(n["online"] for n in current.values()):
            identity = "probe-" + label + "-" + uuid.uuid4().hex
            t0 = time.monotonic()
            command({"action": "submit", "graph": graph(identity, "warmup")})
            while time.monotonic() - t0 < 61:
                state = command({"action": "graph-status", "id": identity})
                result = state["final"]["status"]
                if result in {"completed", "failed", "cancelled", "timed-out", "blocked"}:
                    break
                time.sleep(.15)
            report("M2_AB_GRAPH", {"condition": label, "id": identity, "state": state,
                                   "seconds": time.monotonic() - t0,
                                   "verified": verified(command, identity, True) if result == "completed" else None})
        sample()
    finally:
        run("systemctl", "stop", UNIT)
        trace = STATE / "acceptance-rpc.jsonl"
        if trace.exists():
            for line in trace.read_text().splitlines():
                report("M2_AB_RPC", dict(json.loads(line.removeprefix("M2_RPC ")), condition=label))
        report("M2_AB_END", {"condition": label, "ended": time.monotonic(), "result": result})
    # Do not start another condition while unresolved work could survive.
    if identity is not None and result != "completed":
        raise RuntimeError("Unresolved or failed graph; stopping paired experiment")


if __name__ == "__main__":
    for name in ("A1", "B1", "B2", "A2"):
        condition(name, name.startswith("B"))
