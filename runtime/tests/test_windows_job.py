from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from community_grid.jobs import JobQueue
from community_grid.worker import LocalLlama


@unittest.skipUnless(sys.platform == "win32", "Windows Job Objects")
class WindowsJobTests(unittest.TestCase):
    def test_job_memory_limit_and_kill_on_close(self):
        from community_grid.windows_job import WindowsJob
        job = WindowsJob(128 * 1024**2, 10)
        program = (
            "import sys,time; sys.stdin.readline()\n"
            "try: data=bytearray(256*1024**2)\n"
            "except MemoryError: print('limited',flush=True)\n"
            "time.sleep(30)\n"
        )
        with subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE) as process:
            try:
                job.assign(process)
                process.stdin.write(b"go\n")
                process.stdin.flush()
                # Close or terminate in finally even if a cap assertion fails.
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.communicate(timeout=2)
            finally:
                job.close()
                if process.poll() is None:
                    process.wait(timeout=5)
            output, _ = process.communicate()
            self.assertIn(b"limited", output)

    def test_wall_timeout_and_cancellation(self):
        # Replace only the helper command. Exercise the actual Windows
        # assignment, deadline, cancellation and handle cleanup.
        real_popen = subprocess.Popen
        processes = []

        def launch(command, **options):
            process = real_popen([sys.executable, "-c",
                                 "import sys,time; sys.stdin.read(); time.sleep(30)"], **options)
            processes.append(process)
            return process

        execute = LocalLlama.__new__(LocalLlama)
        execute.backend = execute.model = "unused"
        execute.threads, execute.memory_mib = 1, 256
        with patch("community_grid.worker.subprocess.Popen", side_effect=launch):
            with self.assertRaises(TimeoutError):
                execute("test", lambda: False, 1)
            started = time.monotonic()
            with self.assertRaises(InterruptedError):
                execute("test", lambda: time.monotonic() - started > .2, 3)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_owner_crash_kills_descendant_and_queue_recovers(self):
        import ctypes as c
        from ctypes import wintypes as w
        from community_grid.windows_job import api, checked

        owner_code = """
import subprocess,sys,time
from community_grid.windows_job import WindowsJob
job=WindowsJob(256*1024**2,30)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
job.assign(child)
print(child.pid,flush=True)
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "jobs.sqlite")
            queue = JobQueue(state)
            job_id = queue.submit_inference("test")
            queue.claim_inference()
            with subprocess.Popen([sys.executable, "-c", owner_code], stdout=subprocess.PIPE) as owner:
                pid = int(owner.stdout.readline())
                handle = checked(api("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(0x100000, False, pid))
                try:
                    owner.kill()
                    owner.wait(timeout=5)
                    self.assertEqual(api("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)(handle, 5000), 0)
                finally:
                    api("CloseHandle", [w.HANDLE])(handle)
            reopened = JobQueue(state)
            self.assertEqual(reopened.recover_inference(), 1)
            self.assertEqual(reopened.get(job_id)["status"], "failed")
