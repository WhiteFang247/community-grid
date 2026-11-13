import unittest
from unittest.mock import patch
from community_grid.diagnostics import collect, _run

class DiagnosticsTests(unittest.TestCase):
    def test_missing_command(self):
        with patch("community_grid.diagnostics.shutil.which", return_value=None):
            self.assertFalse(_run(["missing-cmd"])["available"])

    def test_collector_has_fixed_commands(self):
        with patch("community_grid.diagnostics._run", return_value={"available": False}), patch(
            "community_grid.diagnostics.discover", return_value={"schema_version": 1}
        ):
            result = collect()
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("block_devices", result["commands"])
        self.assertNotIn("shell", result["commands"])

if __name__ == "__main__":
    unittest.main()
