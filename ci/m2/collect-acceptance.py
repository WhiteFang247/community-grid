"""Validate and preserve the hosted M2 acceptance evidence even on failure."""
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import statistics

OUT = Path("evidence")
EXPECTED = dict(warmup=5, healthy=30, local=30, lost=20, restart=20, offline=20, cancel=20)


def describe(values):
    values = sorted(values)
    if not values:
        return None
    return dict(n=len(values), median=statistics.median(values),
                p95=values[(95 * len(values) + 99) // 100 - 1], maximum=values[-1])


def main():
    OUT.mkdir(exist_ok=True)
    rows = defaultdict(list)
    missing, failures = [], []
    for role in "AB":
        serial = OUT / role / "command-serial.log"
        if not serial.exists():
            continue
        content = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", serial.read_text(errors="replace"))
        for tag, payload in re.findall(r"(?m)^M2_(TRIAL|VERDICT|RPC|FAULT_RPC|WORKER|STATE) (\{.+\})\s*$", content):
            try:
                rows[tag].append(json.loads(payload))
            except json.JSONDecodeError:
                missing.append(f"Malformed {tag} record on {role}")
    for tag, name in (("TRIAL", "trials"), ("RPC", "rpc"), ("FAULT_RPC", "fault-rpc"),
                      ("WORKER", "worker-attempts"), ("STATE", "graph-state")):
        (OUT / (name + ".jsonl")).write_text("".join(json.dumps(x) + "\n" for x in rows[tag]))
    trials = rows["TRIAL"]
    counts = Counter(x["mode"] for x in trials)
    success = Counter(x["mode"] for x in trials if x["status"] == "pass")
    if counts != EXPECTED:
        missing.append("Required trial counts were not reached")
    if len({x["id"] for x in trials}) != len(trials):
        failures.append("Duplicate trial graph ID")
    if counts != success:
        failures.append("One or more trials failed")
    stats = {mode: describe([x["seconds"] for x in trials
                             if x["mode"] == mode and x["status"] == "pass"]) for mode in EXPECTED}
    if success["healthy"] == EXPECTED["healthy"] and stats["healthy"]["p95"] > 20:
        failures.append("Healthy p95 exceeded 20 seconds")
    rpc = rows["RPC"] + rows["FAULT_RPC"]
    if any(x["seconds"] > 3 for x in rpc):
        failures.append("Observed RPC exceeded 3 seconds")
    if not rows["RPC"]:
        missing.append("Coordinator RPC trace absent")
    workers = {x["attempt"]: x for x in rows["WORKER"]}
    for t in trials:
        if t["status"] != "pass" or t["mode"] == "local":
            continue
        for name, item in t.get("graph_status", {}).items():
            worker = workers.get(item["attempt"], {})
            if worker.get("node") != item["node"] or worker.get("outcome", {}).get("sha256") != item["result"]["sha256"]:
                missing.append(f"Missing or inconsistent worker evidence: {t['id']} {name}")
    boot, settings = [], []
    for role in "AB":
        if (OUT / role / "boot.json").exists():
            boot.append(json.loads((OUT / role / "boot.json").read_text()))
            settings.append(json.loads((OUT / role / "vm.json").read_text()))
    if len(boot) != 2:
        missing.append("Both guest boot records required")
    elif (boot[0]["boot_id"] == boot[1]["boot_id"] or any(x["serial_ready_s"] > 240 for x in boot)
          or len({x["accelerator"] for x in settings}) != 1):
        failures.append("Guest independence, boot time, or acceleration gate failed")
    guest = rows["VERDICT"][-1] if rows["VERDICT"] else None
    if guest and guest["status"] == "FAIL":
        failures.append("Guest trial verdict failed")
    if not guest or guest["status"] != "PASS" or os.environ.get("MATRIX_OUTCOME") != "success":
        missing.append("Complete passing guest and host audit required")
    status = "FAIL" if failures else "INCOMPLETE" if missing else "PASS"
    verdict = dict(status=status, failures=failures, gaps=missing, observed=dict(counts),
                   successful=dict(success), required=EXPECTED, statistics=stats,
                   rpc=dict(success=describe([x["seconds"] for x in rpc if not x.get("error_type")]),
                            errors=sum(bool(x.get("error_type")) for x in rpc)),
                   guest_verdict=guest, vm=settings)
    (OUT / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
    print(json.dumps(verdict, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
