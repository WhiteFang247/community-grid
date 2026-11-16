"""Three clean concurrent discovery rounds and one graph in disposable guest A."""
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
import uuid

from community_grid.native import exchange
from trials import A_SOCKET, graph, report, verified

STATE = Path("/var/lib/cgos-coordinator")
UNIT = "cgos-coordinator.service"
ADDRESSES = {"A": "10.44.0.1", "B": "10.44.0.2"}
TIMEOUTS = {"response-timeout", "process-exit-timeout"}


def control(*args):
    subprocess.run(["systemctl", "--no-pager", *args, UNIT], check=True, timeout=20)


def samples():
    trace = STATE / "readiness.jsonl"
    if not trace.exists():
        return []
    return [json.loads(line) for line in trace.read_text().splitlines(keepends=True) if line.endswith("\n")]


def count_probes(events):
    return Counter(row["address"] for row in events if row["event"] == "probe")


def command(message):
    return exchange(A_SOCKET, message)


def evaluate(events, cutoff, boots):
    selected = [row for row in events if row["event"] == "probe" and row["ended"] <= cutoff]
    accepts = {row["id"]: row for row in events if row["event"] == "accepted" and row["at"] <= cutoff}
    for row in selected:
        reply = accepts.get(row["id"])
        row["accepted"] = bool(reply and reply["address"] == row["address"] and
                               reply["boot_id"] == boots.get(row["address"]))
        row["seconds"] = row["ended"] - row["started"]
    a = [r for r in selected if r["address"] == ADDRESSES["A"]]
    b = [r for r in selected if r["address"] == ADDRESSES["B"]]
    pairs = sum(max(x.get("spawn_returned", float("inf")), y.get("spawn_returned", float("inf"))) <
                min(x["ended"], y["ended"]) for x in a for y in b)
    sweep = sorted((moment, delta) for r in selected if "spawn_returned" in r
                   for moment, delta in ((r["spawn_returned"], 1), (r["ended"], -1)))
    peak = outstanding = 0
    for _, delta in sweep:
        outstanding += delta
        peak = max(peak, outstanding)
    return selected, pairs, peak


def summary(rows):
    latencies = sorted(r["seconds"] for r in rows if r["accepted"])
    return {"observations": len(rows), "accepted": len(latencies),
            "latency_s": ({"min": latencies[0], "median": statistics.median(latencies),
                           "p95": latencies[(95*len(latencies)+99)//100-1], "max": latencies[-1]}
                          if latencies else None),
            "max_observed_s": max((r["seconds"] for r in rows), default=None),
            "deadline_overruns": sum(r["seconds"] > 3 for r in rows),
            "timeouts": sum(r["outcome"] in TIMEOUTS for r in rows),
            "connection_errors": sum(r["outcome"] in {"connection-error", "spawn-or-io-error"} for r in rows),
            "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "returncodes": dict(Counter(str(r["returncode"]) for r in rows))}


def round_probe(number, boots, previous_clean):
    control("stop")
    if STATE.exists():
        STATE.rename(STATE.with_name(STATE.name + f"-readiness-before-{number}"))
    control("start")
    end = time.monotonic() + 65
    prior = nodes = None
    passed, eligible, graph_result = False, False, None
    cutoff = None
    try:
        while time.monotonic() < end:
            try:
                nodes = command({"action": "nodes"})
            except (OSError, ValueError):
                time.sleep(.2)
                continue
            if nodes != prior:
                report("M2_READY_STATE", {"round": number, "at": time.monotonic(), "nodes": nodes})
                prior = nodes
            events = samples()
            counts = count_probes(events)
            # Wait for the coordinator to validate successful responses, even
            # when the observer has already recorded the process's completion.
            pending_accepts = {r["id"] for r in events if r["event"] == "probe" and r["outcome"] == "response"} - {
                r["id"] for r in events if r["event"] == "accepted"}
            if all(counts[a] >= 10 for a in ADDRESSES.values()) and not pending_accepts:
                break
            time.sleep(.2)
        eligible = bool(nodes and set(nodes) == set(ADDRESSES) and all(
            nodes[n]["online"] and nodes[n]["boot_id"] == boots[a]
            and nodes[n]["capabilities"]["slots"] == 1 and nodes[n]["capabilities"]["ram_mib"] == 128
            for n, a in ADDRESSES.items()))
        cutoff = time.monotonic()
        rows, pairs, peak = evaluate(samples(), cutoff, boots)
        passed = (eligible and peak == 2 and pairs >= 2 and all(count_probes(rows)[a] >= 10 for a in ADDRESSES.values())
                  and all(r["accepted"] and r["returncode"] == 0 and r["seconds"] <= 3 for r in rows))
        if passed and previous_clean and number == 3:
            identity = "readiness-" + uuid.uuid4().hex
            started = time.monotonic()
            command({"action": "submit", "graph": graph(identity, "healthy")})
            while time.monotonic() - started < 60:
                states = command({"action": "graph-status", "id": identity})
                if states["final"]["status"] == "completed":
                    seconds = time.monotonic() - started
                    graph_result = {"graph": identity, "seconds": seconds, **verified(command, identity, True)}
                    break
                if states["final"]["status"] in {"failed", "timed-out", "blocked", "cancelled"}:
                    break
                time.sleep(.1)
            report("M2_READY_GRAPH", graph_result or {"graph": identity, "status": "failed", "units": states})
    finally:
        if cutoff is None:
            cutoff = time.monotonic()
        graph_cutoff = time.monotonic()
        control("stop")
        events = samples()
        rows, pairs, peak = evaluate(events, cutoff, boots)
        later, _, _ = evaluate(events, graph_cutoff, boots)
        later = [row for row in later if row["ended"] > cutoff]
        late_failures = [r for r in later if not r["accepted"] or r["returncode"] != 0 or r["seconds"] > 3]
        passed = passed and not late_failures
        report("M2_READY_ROUND", {"round": number, "passed": passed, "overlapping_pairs": pairs,
                                  "peak_outstanding": peak, "eligible": eligible if nodes else False,
                                  "counts": dict(count_probes(rows)), "post_window_failures": late_failures})
        for event in events:
            report("M2_READY_RAW", dict(event, round=number, measured_until=cutoff, monitor_until=graph_cutoff))
        for row in rows:
            report("M2_READY_PROBE", dict(row, round=number))
    return passed, rows, graph_result


if __name__ == "__main__":
    os.environ.update(SYSTEMD_PAGER="cat", SYSTEMD_COLORS="0")
    boots = dict(zip(ADDRESSES.values(), os.sys.argv[1:], strict=True))
    passed = True
    all_rows = []
    graph_result = None
    for number in range(1, 4):
        clean, rows, result = round_probe(number, boots, passed)
        all_rows.extend(rows)
        passed = passed and clean
        graph_result = result or graph_result
    stats = {n: summary([r for r in all_rows if r["address"] == address]) for n, address in ADDRESSES.items()}
    passed = passed and graph_result is not None and all(x["observations"] >= 30 for x in stats.values())
    report("M2_READY_VERDICT", {"status": "GO" if passed else "NO-GO", "workers": stats, "graph": graph_result})
    raise SystemExit(0 if passed else 1)
