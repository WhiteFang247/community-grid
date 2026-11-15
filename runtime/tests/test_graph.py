from pathlib import Path
import tempfile
import unittest

from community_grid.graph import GraphRuntime, SimulatedNode, digest


def unit(op, node, deps=(), **kwargs):
    return dict(op=op, input_type="text", output_type="text", nodes=[node], depends=list(deps),
                cpu=1, ram_mib=128, timeout=10, attempts=2, side_effects="none", **kwargs)


class GraphTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "graph.sqlite"
        self.nodes = {"a": SimulatedNode(), "b": SimulatedNode(ram_mib=64)}
        self.runtime = GraphRuntime(self.path, self.nodes)
        self.graph = {"version": 1, "id": "graph", "owner": "app", "units": {
            "normalize": unit("text.normalize", "a", value="  hello   world  "),
            "hash": unit("text.sha256", "b", ("normalize",), **{"from": "normalize"}),
        }}

    def tick(self):
        self.runtime.tick("graph", "app")

    def record(self, name):
        return self.runtime.status("graph", "app")["units"][name]

    def test_cross_node_restart_and_single_publication(self):
        self.runtime.submit(self.graph, "app")
        self.runtime.submit(self.graph, "app")
        self.tick()
        self.tick()
        node = self.nodes["a"]
        attempt = self.record("normalize")["attempt"]
        node.submit(attempt, "text.normalize", "  hello   world  ", self.record("normalize")["deadline"])
        self.assertEqual(len(node.attempts), 1)
        self.runtime = GraphRuntime(self.path, self.nodes)
        node.advance()
        self.tick()
        self.assertEqual(self.record("hash")["status"], "waiting")
        self.nodes["b"].ram_mib = 256
        self.tick()
        self.tick()
        self.nodes["b"].advance()
        self.tick()
        self.assertEqual(self.runtime.result("graph", "app", "hash"), digest(b"hello world"))
        self.assertEqual(self.record("hash")["node"], "b")
        self.assertFalse(self.runtime.publish("graph", "app", "normalize", attempt, b'"fake"', digest(b'"fake"')))
        with self.assertRaises(KeyError):
            self.runtime.status("graph", "other")

    def test_ambiguous_ack_failure_retry_and_fence(self):
        class LostReply(SimulatedNode):
            def submit(self, *args):
                super().submit(*args)
                raise ConnectionError
        self.nodes["a"] = node = LostReply()
        self.runtime.submit(self.graph, "app")
        self.tick()
        self.tick()
        old = self.record("normalize")["attempt"]
        spec, _ = node.attempts[old]
        node.online = False
        self.tick()
        self.assertEqual(self.record("normalize")["attempt"], old)
        self.runtime = GraphRuntime(self.path, self.nodes)
        node.online = True
        node.attempts[old] = (spec, ("failed", None, None))
        self.tick()
        new = self.record("normalize")["attempt"]
        self.assertNotEqual(new, old)
        self.assertFalse(self.runtime.publish("graph", "app", "normalize", old, b'"late"', digest(b'"late"')))
        with self.assertRaises(ValueError):
            self.runtime.publish("graph", "app", "normalize", new, b'"bad"', "0" * 64)
        self.tick()
        node.advance()
        self.tick()
        self.assertEqual(self.runtime.result("graph", "app", "normalize"), "hello world")

    def test_cancel_and_cycle(self):
        self.graph["units"]["normalize"]["depends"] = ["hash"]
        with self.assertRaises(ValueError):
            self.runtime.submit(self.graph, "app")
        self.graph["units"]["normalize"]["depends"] = []
        self.runtime.submit(self.graph, "app")
        self.tick()
        self.tick()
        self.runtime.cancel("graph", "app")
        self.tick()
        self.assertEqual(self.record("normalize")["status"], "cancelled")
        self.assertEqual(self.record("hash")["status"], "cancelled")

    def test_slot_reserved_across_graphs_and_expired_result_rejected(self):
        now = 0
        self.runtime.clock = lambda: now
        for node in self.nodes.values():
            node.clock = lambda: now
        self.runtime.submit(self.graph, "app")
        self.runtime.submit(dict(self.graph, id="other"), "app")
        self.tick()
        self.runtime.tick("other", "app")
        self.assertEqual(self.runtime.status("other", "app")["units"]["normalize"]["status"], "waiting")
        attempt = self.record("normalize")["attempt"]
        self.tick()
        now = 11
        self.assertFalse(self.runtime.publish("graph", "app", "normalize", attempt, b'"late"', digest(b'"late"')))
        self.tick()
        self.assertEqual(self.record("normalize")["status"], "timed-out")
        self.assertEqual(self.record("hash")["status"], "blocked")
