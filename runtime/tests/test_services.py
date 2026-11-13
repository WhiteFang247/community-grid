import tempfile
import unittest
from pathlib import Path
from community_grid.jobs import JobQueue
from community_grid.models import recommend
from community_grid.server import handle_job

class ServiceTests(unittest.TestCase):
    def test_model_selection_low_memory(self):
        self.assertIsNone(recommend({"memory_total_bytes": 1024})["selected"])

    def test_model_selection_typical(self):
        result = recommend({"memory_total_bytes": 16 * 1024 ** 3})
        self.assertIsNotNone(result["selected"])
        self.assertFalse(result["installation_performed"])

    def test_queue_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "jobs.db")
            queue = JobQueue(path)
            job_id = queue.submit("system.health")
            self.assertEqual(queue.run(job_id, handle_job)["status"], "done")
            self.assertEqual(JobQueue(path).get(job_id)["result"]["mode"], "standalone")

    def test_queue_rejects_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                JobQueue(str(Path(tmp) / "jobs.db")).submit("shell.exec", {"command": "rm -rf /"})

if __name__ == "__main__":
    unittest.main()
