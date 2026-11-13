"""Thread-safe in-memory node registry. No network listener or authentication yet."""
from __future__ import annotations
from datetime import datetime, timezone
from threading import RLock


class NodeRegistry:
    def __init__(self):
        self._nodes = {}
        self._lock = RLock()

    def register(self, node_id: str, capabilities: dict) -> dict:
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node_id must be nonempty")
        if not isinstance(capabilities, dict) or capabilities.get("schema_version") != 1:
            raise ValueError("unsupported capabilities schema")
        entry = {
            "node_id": node_id,
            "capabilities": dict(capabilities),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._nodes[node_id] = entry
        return dict(entry)

    def get(self, node_id: str):
        with self._lock:
            entry = self._nodes.get(node_id)
            return None if entry is None else {"node_id": entry["node_id"], "capabilities": dict(entry["capabilities"]), "last_seen": entry["last_seen"]}

    def list_nodes(self):
        with self._lock:
            ids = sorted(self._nodes)
        return [self.get(node_id) for node_id in ids]
