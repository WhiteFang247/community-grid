"""Run the Phase 1B reference graph with an enrolled SSH worker."""
import argparse
import json
from pathlib import Path
import sys
import time

from .graph import GraphRuntime, digest
from .physical import PhysicalNode, process_transport, ssh_transport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("address")
    parser.add_argument("account")
    parser.add_argument("identity")
    parser.add_argument("known_hosts")
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.resume:
        if not (args.state / 'graph.db').is_file():
            parser.error('No coordinator database to resume')
    else:
        args.state.mkdir()
    nodes = {
        "lenovo": PhysicalNode(process_transport([
            sys.executable, "-m", "community_grid.physical", str(args.state / "lenovo.db")
        ])),
        "dell": PhysicalNode(ssh_transport(args.address, args.account, args.identity, args.known_hosts)),
    }
    runtime = GraphRuntime(args.state / "graph.db", nodes)
    graph = {"id": "phase1b", "owner": "operator", "version": 1, "timeout": 30, "units": {
        "normalize": {"op": "text.normalize", "nodes": ["lenovo"], "value": "  seed   library  ",
                      "depends": [], "cpu": 1, "ram_mib": 32, "timeout": 10, "attempts": 1, "input_type": "text", "output_type": "text", "side_effects": "none"},
        "hash": {"op": "text.sha256", "nodes": ["dell"], "from": "normalize",
                 "depends": ["normalize"], "cpu": 1, "ram_mib": 32, "timeout": 10, "attempts": 1, "input_type": "text", "output_type": "text", "side_effects": "none"},
    }}
    start = time.monotonic()
    runtime.submit(graph, "operator")
    while time.monotonic() - start < 45:
        runtime.tick("phase1b", "operator")
        state = runtime.status("phase1b", "operator")
        if all(unit["status"] in {"completed", "failed", "cancelled", "timed-out", "blocked"}
               and not unit.get("cleanup_pending") for unit in state["units"].values()):
            break
        time.sleep(0.1)
    print(json.dumps({"elapsed_seconds": time.monotonic() - start, "state": state}, indent=2))
    if state["units"]["hash"]["status"] != "completed":
        return 1
    return int(runtime.result("phase1b", "operator", "hash") != digest(b"seed library"))


if __name__ == "__main__":
    raise SystemExit(main())
