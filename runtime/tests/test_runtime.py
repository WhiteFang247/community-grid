import unittest
from unittest.mock import patch
from community_grid.hardware import discover
from community_grid.registry import NodeRegistry


class RuntimeTests(unittest.TestCase):
    def test_discovery_schema(self):
        with patch("community_grid.hardware._gpu_devices", return_value=[]):
            result = discover()
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("architecture", result)
        self.assertIn("memory_total_bytes", result)

    def test_register_and_lookup(self):
        registry = NodeRegistry()
        registry.register("lenovo", {"schema_version": 1, "architecture": "x86_64"})
        self.assertEqual(registry.get("lenovo")["capabilities"]["architecture"], "x86_64")
        self.assertEqual(len(registry.list_nodes()), 1)

    def test_reject_invalid(self):
        with self.assertRaises(ValueError):
            NodeRegistry().register("", {"schema_version": 1})
        with self.assertRaises(ValueError):
            NodeRegistry().register("dell", {"schema_version": 2})


if __name__ == "__main__":
    unittest.main()
