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
        raise ValueError("Graph timeout must be 1Ã¢â‚¬â€œ300 seconds")
    if type(graph["version"]) is not int or graph["version"] != 1 or not isinstance(graph["owner"], str) or not graph["owner"].strip():
        raise ValueError("Invalid version or owner")
    if not isinstance(graph["id"], str) or not graph["id"]:
        raise ValueError("Invalid graph identity")
    units = graph["units"]
    if not isinstance(units, dict) or not 1 <= len(units) <= 32:
        raise ValueError("Expected 1Ã¢â‚¬â€œ32 units")
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
    def __init__(self, path, nodes, clock=None, *, coordinator_boot_id=None,
                 lease_seconds=15, worker_memory_mib=128):
        if coordinator_boot_id is not None and (not isinstance(coordinator_boot_id, str)
                                                or not coordinator_boot_id):
            raise ValueError("Invalid coordinator boot ID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid lease duration")
        if type(worker_memory_mib) is not int or worker_memory_mib < 1:
            raise ValueError("Invalid worker memory budget")
        self.path, self.nodes = path, nodes
        self.boot_id = coordinator_boot_id
        self.clock = clock or (time.monotonic if self.boot_id else time.time)
        self.lease_seconds = lease_seconds
        self.worker_memory_mib = worker_memory_mib
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS graphs(id TEXT PRIMARY KEY, state BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY, data BLOB NOT NULL);
            """)
            if self.boot_id:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS scheduler_epoch(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1), boot_id TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS lease_quarantine(
                        attempt TEXT PRIMARY KEY, node TEXT NOT NULL, lease BLOB NOT NULL);
                """)
                db.execute("BEGIN IMMEDIATE")
                epoch = db.execute("SELECT boot_id FROM scheduler_epoch").fetchone()
                if epoch != (self.boot_id,):
                    for (raw,) in db.execute("SELECT state FROM graphs").fetchall():
                        state = json.loads(raw)
                        if not state.get("coordinator_boot_id"):
                            continue
                        for record in state["units"].values():
                            if record["status"] == "assigned":
                                self._quarantine(db, record["lease"])
                                record["history"][-1]["outcome"] = "coordinator-restarted"
                            if record["status"] not in TERMINAL:
                                record["status"] = "timed-out"
                        self._save(db, state)
                    db.execute("INSERT OR REPLACE INTO scheduler_epoch VALUES (1, ?)",
                               (self.boot_id,))

    def set_enrollment(self, enrollment):
        """Fence leases transactionally when administrator trust changes."""
        if not self.boot_id:
            raise ValueError("Enrollment requires lease mode")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_epoch(db)
            db.execute("CREATE TABLE IF NOT EXISTS enrollment(node TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            previous = dict(db.execute("SELECT node,fingerprint FROM enrollment"))
            changed = {node for node, fingerprint in previous.items() if enrollment.get(node) != fingerprint}
            for (raw,) in db.execute("SELECT state FROM graphs").fetchall():
                state = json.loads(raw)
                if state.get("coordinator_boot_id") != self.boot_id:
                    continue
                dirty = False
                for name, record in state["units"].items():
                    if record["status"] != "assigned" or record["node"] not in changed:
                        continue
                    self._quarantine(db, record["lease"])
                    record["history"][-1]["outcome"] = "enrollment-revoked"
                    limit = state["graph"]["units"][name]["attempts"]
                    record["status"] = ("cancelled" if record.get("cancel_requested") else
                                        "waiting" if record["lease"]["generation"] < limit
                                        and self.clock() < state["deadline"] else "failed")
                    dirty = True
                if dirty:
                    self._save(db, state)
            db.execute("DELETE FROM enrollment")
            db.executemany("INSERT INTO enrollment VALUES (?,?)", enrollment.items())

    def _assert_epoch(self, db):
        if self.boot_id and db.execute("SELECT boot_id FROM scheduler_epoch").fetchone() != (self.boot_id,):
            raise RuntimeError("Coordinator boot identity is fenced")

    def _quarantine(self, db, lease):
        db.execute("INSERT OR IGNORE INTO lease_quarantine VALUES (?, ?, ?)",
                   (lease["attempt"], lease["node"], encode(lease)))

    def _load(self, db, graph_id, owner):
        self._assert_epoch(db)
        row = db.execute("SELECT state FROM graphs WHERE id=?", (graph_id,)).fetchone()
        if (row is None or (state := json.loads(row[0]))["graph"]["owner"] != owner
                or bool(state.get("coordinator_boot_id")) != bool(self.boot_id)):
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
            self._assert_epoch(db)
            row = db.execute("SELECT state FROM graphs WHERE id=?", (graph["id"],)).fetchone()
            if row:
                previous = json.loads(row[0])
                if previous["graph"] != graph or bool(previous.get("coordinator_boot_id")) != bool(self.boot_id):
                    raise ValueError("Graph ID conflict")
            else:
                state = {"graph": graph, "deadline": self.clock() + graph.get("timeout", 120),
                         "units": {name: {"status": "waiting", "history": []}
                                                   for name in graph["units"]}}
                if self.boot_id:
                    state["coordinator_boot_id"] = self.boot_id
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

    def publish(self, graph_id, owner, unit, attempt, data, checksum, *, lease=None, worker_id=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            record = state["units"][unit]
            if (record["status"] != "assigned" or record["attempt"] != attempt
                    or record.get("cancel_requested") or self.clock() >= record["deadline"]):
                return False
            if self.boot_id:
                expected_lease = record["lease"]
                assigned_node = self.nodes.get(expected_lease["node"])
                if (state["coordinator_boot_id"] != self.boot_id
                        or worker_id != expected_lease["node"]
                        or encode(lease) != encode(expected_lease)
                        or getattr(assigned_node, "boot_id", None) != expected_lease["worker_boot_id"]
                        or self.clock() >= state["deadline"]
                        or digest(encode(record["input"])) != expected_lease["input_digest"]):
                    return False
            expected = OPERATIONS[state["graph"]["units"][unit]["op"]][1]
            if len(data) > 65536 or digest(data) != checksum or type(json.loads(data)) is not expected:
                raise ValueError("Invalid result manifest")
            operation = state["graph"]["units"][unit]["op"]
            if data != encode(OPERATIONS[operation][2](record["input"])):
                raise ValueError("Result differs from trusted local execution")
            if self.boot_id and self.clock() >= min(state["deadline"], record["deadline"]):
                return False
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (checksum, data))
            self._artifact(db, checksum)
            record.update(status="completed", result={"attempt": attempt, "sha256": checksum,
                                                       "size": len(data), "type": TYPES[expected]})
            record["history"][-1]["outcome"] = "completed"
            self._save(db, state)
            return True

    def tick(self, graph_id, owner):
        state = self.status(graph_id, owner)
        if self.boot_id:
            self.reconcile_quarantine()
        for name, record in state["units"].items():
            if record["status"] == "assigned" or record.get("cleanup_pending"):
                if self.boot_id:
                    self._reconcile_lease(graph_id, owner, name, record)
                else:
                    self._reconcile(graph_id, owner, name, record)
        state = self.status(graph_id, owner)
        eligible = {
            name: [node for node in sorted(set(unit["nodes"]))
                   if node in self.nodes and self._eligible(self.nodes[node], unit)]
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
            if self.boot_id:
                occupied.update(node for (node,) in db.execute("SELECT node FROM lease_quarantine"))
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
                budget = min(unit["timeout"], self.lease_seconds) if self.boot_id else unit["timeout"]
                assigned_at = self.clock()
                deadline = min(assigned_at + budget, state.get("deadline", float("inf")))
                if self.boot_id and deadline <= assigned_at:
                    record["status"] = "timed-out"
                    continue
                value = (self._artifact(db, state["units"][unit["from"]]["result"]["sha256"])
                         if "from" in unit else unit["value"])
                for field in ("acknowledged", "acknowledged_at", "started_at", "finished_at", "cleanup_pending"):
                    record.pop(field, None)
                record.update(status="assigned", node=node, attempt=attempt, deadline=deadline, input=value,
                              reason="First eligible node by ID")
                record["history"].append({"attempt": attempt, "node": node, "deadline": deadline,
                                          "assigned_at": self.clock()})
                if self.boot_id:
                    lease = dict(graph=graph_id, task=name, node=node,
                                 worker_boot_id=self.nodes[node].boot_id,
                                 coordinator_boot_id=self.boot_id,
                                 generation=len(record["history"]), attempt=attempt,
                                 deadline=deadline, budget_seconds=deadline - assigned_at,
                                 cpu=unit["cpu"], ram_mib=unit["ram_mib"],
                                 operation=unit["op"], input_digest=digest(encode(value)))
                    lease["request_digest"] = digest(encode(lease))
                    record["lease"] = record["history"][-1]["lease"] = lease
                occupied.add(node)
            self._save(db, state)

    def _eligible(self, node, unit):
        if self.boot_id and (not getattr(node, "boot_id", None) or unit["cpu"] != 1
                             or unit["ram_mib"] > self.worker_memory_mib):
            return False
        return node.eligible(unit)

    def _finish_lease(self, graph_id, owner, name, lease, outcome, *, retry=False, quarantine=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._load(db, graph_id, owner)
            record = state["units"][name]
            if record["status"] != "assigned" or record["lease"] != lease:
                return
            if record.get("cancel_requested"):
                outcome, retry = "cancelled", False
            if quarantine:
                self._quarantine(db, lease)
            record["history"][-1]["outcome"] = outcome
            record["status"] = ("waiting" if retry and self.clock() < state["deadline"]
                                and lease["generation"] < state["graph"]["units"][name]["attempts"]
                                else outcome)
            self._save(db, state)

    @staticmethod
    def _lease_reply(node, lease):
        reply = node.lease_status(lease)
        if (not isinstance(reply, dict) or encode(reply.get("lease")) != encode(lease)
                or reply.get("status") not in
                {"missing", "queued", "running", "completed", "failed", "cancelled", "timed-out"}):
            raise ValueError("Invalid lease response")
        return reply

    def _reconcile_lease(self, graph_id, owner, name, record):
        lease = record["lease"]
        node = self.nodes.get(lease["node"])
        if record.get("cancel_requested") or self.clock() >= lease["deadline"]:
            outcome = "cancelled" if record.get("cancel_requested") else "timed-out"
            self._finish_lease(graph_id, owner, name, lease, outcome,
                               retry=outcome == "timed-out", quarantine=True)
            return
        if node is None or not getattr(node, "boot_id", None):
            return
        if node.boot_id != lease["worker_boot_id"]:
            self._finish_lease(graph_id, owner, name, lease, "failed", retry=True)
            return
        try:
            reply = self._lease_reply(node, lease)
            status = reply["status"]
            if status == "missing" and not record.get("acknowledged"):
                if self.clock() < lease["deadline"]:
                    node.lease_dispatch(lease, record["input"])
                    self._observe(graph_id, owner, name, lease["attempt"], acknowledged=True)
            elif status in {"queued", "running"}:
                self._observe(graph_id, owner, name, lease["attempt"], acknowledged=True)
            elif status == "completed":
                self.publish(graph_id, owner, name, lease["attempt"],
                             reply["data"], reply["sha256"], lease=reply["lease"],
                             worker_id=lease["node"])
            else:
                self._finish_lease(graph_id, owner, name, lease,
                                   "failed" if status == "missing" else status, retry=status != "cancelled",
                                   quarantine=status == "missing")
        except ConnectionError:
            return
        except (ValueError, TypeError, KeyError):
            self._finish_lease(graph_id, owner, name, lease, "failed", quarantine=True)

    def reconcile_quarantine(self):
        with closing(sqlite3.connect(self.path)) as db:
            self._assert_epoch(db)
            rows = db.execute("SELECT attempt, node, lease FROM lease_quarantine").fetchall()
        for attempt, node_id, raw in rows:
            lease, node = json.loads(raw), self.nodes.get(node_id)
            if node is None or not getattr(node, "boot_id", None):
                continue
            try:
                if node.boot_id == lease["worker_boot_id"]:
                    node.lease_cancel(lease)
                    if self._lease_reply(node, lease)["status"] not in TERMINAL:
                        continue
            except (ConnectionError, ValueError, TypeError, KeyError):
                continue
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._assert_epoch(db)
                db.execute("DELETE FROM lease_quarantine WHERE attempt=?", (attempt,))

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
