"""CGOS local graph service and console."""
import argparse
from contextlib import contextmanager
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import platform
import signal
import socket
import socketserver
import sqlite3

from .graph import GraphRuntime, TERMINAL, encode
from .physical import LIMIT, PhysicalNode, handle

SOCKET = "/run/cgos/api.sock"
MAX_ACTIVE = 16


class Runtime:
    def __init__(self, state, registry=None, *, boot_id=None):
        self.registry = registry
        if registry is None:
            self.path = state / "graphs.db"
            worker = state / "attempts.db"
            self.node = PhysicalNode(lambda request: handle(worker, request))
            self.graphs = GraphRuntime(self.path, {"local": self.node})
        else:
            self.path = state / "distributed-graphs.db"
            boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            self.graphs = GraphRuntime(self.path, registry.peers, coordinator_boot_id=boot_id)
            registry.load(self.graphs)

    def close(self):
        if self.registry is not None:
            self.registry.close()

    def active(self):
        with closing(sqlite3.connect(self.path)) as db:
            for (raw,) in db.execute("SELECT state FROM graphs ORDER BY id"):
                state = json.loads(raw)
                if any(unit["status"] not in TERMINAL or unit.get("cleanup_pending")
                       for unit in state["units"].values()):
                    yield state["graph"]

    def request(self, request):
        action = request["action"]
        if action == "hardware":
            cpu = Path("/proc/cpuinfo")
            memory = Path("/proc/meminfo")
            model = next((line.split(":", 1)[1].strip() for line in cpu.read_text().splitlines()
                          if line.startswith("model name")), None) if cpu.exists() else platform.processor()
            total = next((int(line.split()[1]) * 1024 for line in memory.read_text().splitlines()
                          if line.startswith("MemTotal:")), None) if memory.exists() else None
            return {"architecture": platform.machine(), "kernel": platform.release(),
                    "logical_cpus": os.cpu_count(), "processor": model or None, "memory_bytes": total}
        if action == "reload-nodes" and self.registry is not None:
            self.registry.load(self.graphs)
            return self.request({"action": "nodes"})
        if action == "nodes":
            if self.registry is not None:
                return {node: peer.describe() for node, peer in self.registry.peers.items()}
            return {"local": self.node.transport({"action": "probe"}), "remote_enrollment": "unsupported"}
        if action in ("status", "diagnostics"):
            result = {"service": "ready", "active_graphs": sum(1 for _ in self.active())}
            if action == "diagnostics":
                result.update(hardware=self.request({"action": "hardware"}),
                              nodes=self.request({"action": "nodes"}), storage=str(self.path))
            return result
        if action == "submit":
            graph = request["graph"]
            if graph.get("owner") != "local":
                raise ValueError("Graph owner must be local")
            if self.registry is None:
                operations = self.request({"action": "nodes"})["local"]["operations"]
                for unit in graph["units"].values():
                    if unit["nodes"] != ["local"] or unit["op"] not in operations or not self.node.eligible(unit):
                        raise ValueError("Unsupported local placement or resource request")
            else:
                for unit in graph["units"].values():
                    for node in unit["nodes"]:
                        peer = self.registry.peers.get(node)
                        if (peer is None or unit["op"] not in peer.policy["operations"]
                                or unit["cpu"] != 1 or unit["ram_mib"] > peer.policy["memory_mib"]):
                            raise ValueError("Unknown worker or request outside enrollment policy")
            if sum(1 for _ in self.active()) >= MAX_ACTIVE:
                raise ValueError("Active graph limit reached")
            identity = self.graphs.submit(graph, "local")
            logging.info("%s", json.dumps({"event": "graph_admitted", "graph": identity}))
            return {"id": identity}
        identity = request["id"]
        if action == "graph-cancel" and self.registry is not None:
            self.graphs.cancel(identity, "local")
            return {"id": identity}
        if action == "graph-status":
            state = self.graphs.status(identity, "local")
            keys = {"status", "attempt", "result", "deadline"}
            if self.registry is not None:
                keys.add("node")
            return {name: {key: value for key, value in unit.items() if key in keys}
                    for name, unit in state["units"].items()}
        if action == "result":
            unit = request["unit"]
            value = self.graphs.result(identity, "local", unit)
            manifest = self.graphs.status(identity, "local")["units"][unit]["result"]
            return {"value": value, "artifact": manifest}
        raise ValueError("Unsupported operation")

    def tick(self):
        if self.registry is not None:
            self.registry.tick()
        active = list(self.active())
        if self.registry is not None and not active:
            self.graphs.reconcile_quarantine()
        leased = set()
        for graph in active:
            leased.update(self.graphs.tick(graph["id"], graph["owner"]))
        if self.registry is not None:
            self.registry.refresh(leased)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(1)
        try:
            raw = self.rfile.readline(LIMIT + 1)
            if not raw.endswith(b"\n") or len(raw) > LIMIT:
                raise ValueError("Invalid or oversized request")
            result = {"ok": self.server.runtime.request(json.loads(raw))}
        except (ValueError, TypeError, KeyError, AttributeError, PermissionError, RecursionError):
            result = {"error": "Invalid request, unsupported operation, or result unavailable"}
        except (ConnectionResetError, TimeoutError):
            return
        try:
            self.wfile.write(encode(result) + b"\n")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass


def exchange(path, request):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(path)
        client.sendall(encode(request) + b"\n")
        with client.makefile("rb") as stream:
            response = json.loads(stream.readline(2 * LIMIT))
    if "error" in response:
        raise ValueError(response["error"])
    return response["ok"]


@contextmanager
def coordinator_owner(state, distributed):
    if not distributed:
        yield
        return
    import fcntl
    with (state / "distributed-coordinator.lock").open("ab") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def serve(path, state, enrollment=None):
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    with coordinator_owner(state, enrollment is not None):
        registry = None
        if enrollment is not None:
            from .enrollment import Registry
            registry = Registry(enrollment, state)
        try:
            _serve_loop(path, state, registry, lambda: stopping)
        finally:
            if registry is not None:
                registry.close()


def _serve_loop(path, state, registry, stopping):
    runtime = Runtime(state, registry)
    with socketserver.UnixStreamServer(path, Handler) as server:
        try:
            os.chmod(path, 0o660)
            server.runtime = runtime
            server.timeout = 0.1
            logging.info('{"event":"service_ready"}')
            while not stopping():
                runtime.tick()
                server.handle_request()
        finally:
            Path(path).unlink(missing_ok=True)
            logging.info('{"event":"service_stopped"}')



def main():
    parser = argparse.ArgumentParser(prog="cgos")
    parser.add_argument("--socket", default=SOCKET)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "hardware", "nodes", "diagnostics", "reload-nodes"):
        commands.add_parser(action)
    daemon = commands.add_parser("serve")
    daemon.add_argument("--state", type=Path, required=True)
    daemon.add_argument("--enrollment", type=Path)
    graph = commands.add_parser("graph").add_subparsers(dest="operation", required=True)
    graph.add_parser("submit").add_argument("file", type=Path)
    graph.add_parser("status").add_argument("id")
    graph.add_parser("cancel").add_argument("id")
    result = graph.add_parser("result")
    result.add_argument("id")
    result.add_argument("unit")
    args = parser.parse_args()
    if args.action == "serve":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        serve(args.socket, args.state, args.enrollment)
        return
    request = {"action": args.action}
    try:
        if args.action == "graph":
            request["action"] = {"status": "graph-status", "cancel": "graph-cancel"}.get(
                args.operation, args.operation)
            if args.operation == "submit":
                with args.file.open("rb") as source:
                    request["graph"] = json.loads(source.read(LIMIT + 1))
            else:
                request["id"] = args.id
                if args.operation == "result":
                    request["unit"] = args.unit
        print(json.dumps(exchange(args.socket, request), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"cgos: {exc}\n")


if __name__ == "__main__":
    main()
