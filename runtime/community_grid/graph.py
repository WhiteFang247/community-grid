"""Phase 1A graph API and reference scheduler (single coordinator owner)."""
from contextlib import closing
import hashlib
import json
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def aggregate(value):
    return {"items": value}


OPERATIONS = {
    "text.normalize": (str, str, lambda value: " ".join(value.split())),
    "text.sha256": (str, str, lambda value: digest(value.encode())),
    "data.aggregate": (list, dict, aggregate),
}
TYPES = {str: "text", list: "array", dict: "object"}
TERMINAL = {"completed", "failed", "cancelled", "timed-out", "blocked"}


def validate(graph):
    duration = graph.get("timeout", 120)
    if type(duration) is not int or not 1 <= duration <= 300:
        raise ValueError("Graph timeout must be 1–300 seconds")
    if type(graph["version"]) is not int or graph["version"] != 1 or not isinstance(graph["owner"], str) or not graph["owner"].strip():
        raise ValueError("Invalid version or owner")
    if not isinstance(graph["id"], str) or not graph["id"]:
        raise ValueError("Invalid graph identity")
    units = graph["units"]
    if not isinstance(units, dict) or not 1 <= len(units) <= 32:
        raise ValueError("Expected 1–32 units")
    visited, active = set(), set()

    def visit(name):
        if name in active or name not in units:
            raise ValueError("Cyclic or missing dependency")
        if name in visited:
            return
        active.add(name)
        unit = units[name]
        if unit["op"] not in OPERATIONS:
            raise ValueError("Unknown operation")
        source, target, _ = OPERATIONS[unit["op"]]
        if unit["input_type"] != TYPES[source] or unit["output_type"] != TYPES[target]:
            raise ValueError("Invalid operation types")
        dependencies = unit["depends"]
        if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies):
            raise ValueError("Invalid dependencies")
        for dep in dependencies:
            visit(dep)
        if "from" in unit:
            parent = unit["from"]
            if "value" in unit or parent not in dependencies or units[parent]["output_type"] != unit["input_type"]:
                raise ValueError("Incompatible input reference")
        elif type(unit["value"]) is not source:
            raise ValueError("Invalid literal input type")
        if not isinstance(unit["nodes"], list) or not unit["nodes"] or not all(
            isinstance(node, str) and node for node in unit["nodes"]
        ):
            raise ValueError("Expected eligible nodes")
        for key, hi in (("cpu", 64), ("ram_mib", 1048576), ("timeout", 300), ("attempts", 3)):
            if type(unit[key]) is not int or not 1 <= unit[key] <= hi:
                raise ValueError(f"Invalid {key}")
        if unit["side_effects"] != "none":
            raise ValueError("Only pure operations are supported")
        active.remove(name)
        visited.add(name)

    for name in units:
        visit(name)


class GraphRuntime:
    def __init__(self, path, nodes, clock=time.time):
        self.path, self.nodes, self.clock = path, nodes, clock
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS graphs(id TEXT PRIMARY KEY, state BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY, data BLOB NOT NULL);
            """)

    def _load(self, db, graph_id, owner):
        row = db.execute("SELECT state FROM graphs WHERE id=?", (graph_id,)).fetchone()
        if row is None or (state := json.loads(row[0]))["graph"]["owner"] != owner:
            raise KeyError(graph_id)
        return state

    def submit(self, graph, owner):
        validate(graph)
        if graph["owner"] != owner:
            raise PermissionError("Owner mismatch")
        if len(encode(graph)) > 65536:
            raise ValueError("Graph exceeds 64 KiB")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM graphs WHERE id=?", (graph["id"],)).fetchone()
            if row:
                if json.loads(row[0])["graph"] != graph:
                    raise ValueError("Graph ID conflict")
            else:
                state = {"graph": graph, "deadline": self.clock() + graph.get("timeout", 120),
                         "units": {name: {"status": "waiting", "history": []}
                                                   for name in graph["units"]}}
                db.execute("INSERT INTO graphs VALUES (?,?)", (graph["id"], encode(state)))
        return graph["id"]

    def status(self, graph_id, owner):
        with closing(sqlite3.connect(self.path)) as db:
            return self._load(db, graph_id, owner)

    def _save(self, db, state):
        db.execute("UPDATE graphs SET state=? WHERE id=?", (encode(state), state["graph"]["id"]))

    def _artifact(self, db, checksum):
        row = db.execute("SELECT data FROM artifacts WHERE hash=?", (checksum,)).fetchone()
        if row is None or digest(row[0]) != checksum:
            raise ValueError("Missing or invalid artifact")
        return json.loads(row[0])

    def result(self, graph_id, owner, unit):
        with closing(sqlite3.connect(self.path)) as db:
            record = self._load(db, graph_id, owner)["units"][unit]
            if record["status"] != "completed":
                raise ValueError("Unit has no published result")
            return self._artifact(db, record["result"]["sha256"])

    def cancel(self, graph_id, owner):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            for record in state["units"].values():
                if record["status"] not in TERMINAL:
                    record["cancel_requested"] = True
                    if record["status"] == "waiting":
                        record["status"] = "cancelled"
            self._save(db, state)

    def publish(self, graph_id, owner, unit, attempt, data, checksum):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            record = state["units"][unit]
            if (record["status"] != "assigned" or record["attempt"] != attempt
                    or record.get("cancel_requested") or self.clock() >= record["deadline"]):
                return False
            expected = OPERATIONS[state["graph"]["units"][unit]["op"]][1]
            if len(data) > 65536 or digest(data) != checksum or type(json.loads(data)) is not expected:
                raise ValueError("Invalid result manifest")
            operation = state["graph"]["units"][unit]["op"]
            if data != encode(OPERATIONS[operation][2](record["input"])):
                raise ValueError("Result differs from trusted local execution")
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (checksum, data))
            self._artifact(db, checksum)
            record.update(status="completed", result={"attempt": attempt, "sha256": checksum,
                                                       "size": len(data), "type": TYPES[expected]})
            record["history"][-1]["outcome"] = "completed"
            self._save(db, state)
            return True

    def tick(self, graph_id, owner):
        state = self.status(graph_id, owner)
        for name, record in state["units"].items():
            if record["status"] == "assigned" or record.get("cleanup_pending"):
                self._reconcile(graph_id, owner, name, record)
        state = self.status(graph_id, owner)
        eligible = {
            name: [node for node in sorted(set(unit["nodes"]))
                   if node in self.nodes and self.nodes[node].eligible(unit)]
            for name, unit in state["graph"]["units"].items()
            if state["units"][name]["status"] == "waiting"
            and self.clock() < state.get("deadline", float("inf"))
            and all(state["units"][dep]["status"] == "completed" for dep in unit["depends"])
        }
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            occupied = {record["node"] for row in db.execute("SELECT state FROM graphs")
                        for record in json.loads(row[0])["units"].values()
                        if record["status"] == "assigned" or record.get("cleanup_pending")}
            for name, unit in sorted(state["graph"]["units"].items()):
                record = state["units"][name]
                if record["status"] != "waiting":
                    continue
                if self.clock() >= state.get("deadline", float("inf")):
                    record["status"] = "timed-out"
                    continue
                parents = [state["units"][dep]["status"] for dep in unit["depends"]]
                if any(status in TERMINAL - {"completed"} for status in parents):
                    record["status"] = "blocked"
                    continue
                if any(status != "completed" for status in parents):
                    continue
                candidates = [node for node in eligible.get(name, []) if node not in occupied]
                if not candidates:
                    record["reason"] = "No eligible available slot"
                    continue
                node = candidates[0]
                attempt = str(uuid.uuid4())
                deadline = min(self.clock() + unit["timeout"], state.get("deadline", float("inf")))
                value = (self._artifact(db, state["units"][unit["from"]]["result"]["sha256"])
                         if "from" in unit else unit["value"])
                for field in ("acknowledged", "acknowledged_at", "started_at", "finished_at", "cleanup_pending"):
                    record.pop(field, None)
                record.update(status="assigned", node=node, attempt=attempt, deadline=deadline, input=value,
                              reason="First eligible node by ID")
                record["history"].append({"attempt": attempt, "node": node, "deadline": deadline,
                                          "assigned_at": self.clock()})
                occupied.add(node)
            self._save(db, state)

    def _reconcile(self, graph_id, owner, name, record):
        node = self.nodes.get(record["node"])
        attempt = record["attempt"]
        now = self.clock()
        ending = ("cancelled" if record.get("cancel_requested") else
                  "timed-out" if now >= record["deadline"] else None)
        try:
            if node is None:
                raise ConnectionError
            if ending or record.get("cleanup_pending"):
                node.cancel(attempt)
            status, data, checksum = node.status(attempt)
            if (status == "missing" and not ending and not record.get("acknowledged")
                    and not record.get("cleanup_pending")):
                graph = self.status(graph_id, owner)["graph"]
                unit = graph["units"][name]
                if hasattr(node, "dispatch"):
                    node.dispatch(attempt, unit, record["input"], record["deadline"])
                else:
                    node.submit(attempt, unit["op"], record["input"], record["deadline"])
                self._observe(graph_id, owner, name, attempt, acknowledged=True)
                return
            if status != "missing" and hasattr(node, "execution_times"):
                self._observe(graph_id, owner, name, attempt, acknowledged=True,
                              **node.execution_times)
            if record.get("cleanup_pending"):
                if status != "running":
                    self._observe(graph_id, owner, name, attempt, cleanup_pending=False)
                return
            if status == "completed" and not ending:
                try:
                    self.publish(graph_id, owner, name, attempt, data, checksum)
                except ValueError:
                    self._retire(graph_id, owner, name, attempt, "failed", retry=False)
                return
            if status in ("missing", "completed", "failed", "cancelled", "timed-out"):
                self._retire(graph_id, owner, name, attempt,
                             ending or ("failed" if status == "missing" else status), retry=not ending)
        except ConnectionError:
            if ending:
                self._observe(graph_id, owner, name, attempt, status=ending, cleanup_pending=True)
            return
        except (ValueError, TypeError, KeyError):
            # An invalid peer response cannot release an unconfirmed slot.
            self._observe(graph_id, owner, name, attempt, status=ending or "failed",
                          cleanup_pending=True, reason="Invalid worker response")

    def _observe(self, graph_id, owner, name, attempt, **fields):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            current = state["units"][name]
            if current["attempt"] == attempt:
                if fields.get("acknowledged") and not current.get("acknowledged"):
                    fields["acknowledged_at"] = self.clock()
                current.update(fields)
                current["history"][-1].update(fields)
                self._save(db, state)

    def _retire(self, graph_id, owner, name, attempt, status, retry):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            record = state["units"][name]
            if record["status"] == "assigned" and record["attempt"] == attempt:
                if record.get("cancel_requested"):
                    status, retry = "cancelled", False
                record["history"][-1]["outcome"] = status
                record.pop("acknowledged", None)
                record["status"] = ("waiting" if retry and status == "failed"
                                    and len(record["history"]) < state["graph"]["units"][name]["attempts"] else status)
                self._save(db, state)


class SimulatedNode:
    def __init__(self, cpu=1, ram_mib=512, operations=OPERATIONS, clock=time.time):
        self.cpu, self.ram_mib, self.operations, self.clock = cpu, ram_mib, operations, clock
        self.online = True
        self.attempts = {}

    def eligible(self, unit):
        return (self.online and self.cpu >= unit["cpu"] and self.ram_mib >= unit["ram_mib"]
                and unit["op"] in self.operations)

    def submit(self, attempt, operation, value, deadline):
        if not self.online:
            raise ConnectionError
        spec = (operation, value, deadline)
        if attempt in self.attempts:
            if self.attempts[attempt][0] != spec:
                raise ValueError("Attempt identity conflict")
            return
        if operation not in self.operations or any(outcome[0] == "running" for _, outcome in self.attempts.values()):
            raise ValueError("Operation unavailable or slot already occupied")
        self.attempts[attempt] = (spec, ("running", None, None))

    def advance(self):
        for attempt, (spec, outcome) in list(self.attempts.items()):
            if outcome[0] != "running" or not self.online:
                continue
            op, value, deadline = spec
            if self.clock() >= deadline:
                result = ("timed-out", None, None)
            else:
                data = encode(self.operations[op][2](value))
                result = ("completed", data, digest(data))
            self.attempts[attempt] = (spec, result)

    def status(self, attempt):
        if not self.online:
            raise ConnectionError
        return self.attempts.get(attempt, (None, ("missing", None, None)))[1]

    def cancel(self, attempt):
        if not self.online:
            raise ConnectionError
        if attempt in self.attempts and self.attempts[attempt][1][0] == "running":
            self.attempts[attempt] = (self.attempts[attempt][0], ("cancelled", None, None))
