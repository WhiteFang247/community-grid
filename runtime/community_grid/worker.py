"""Explicitly enabled, single-slot inference worker."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Thread
import time

from . import inference


class Worker:
    def __init__(self, queue, execute, timeout):
        self.queue = queue
        self.execute = execute
        self.timeout = timeout
        self.stop = Event()
        self.thread = Thread(target=self._run, name="inference-worker")

    def start(self):
        self.queue.recover_inference()
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join()

    def _run(self):
        while not self.stop.is_set():
            job = self.queue.claim_inference()
            if job is None:
                self.stop.wait(0.15)
                continue
            job_id = job["id"]

            def cancelled():
                return self.stop.is_set() or self.queue.get(job_id)["status"] == "cancelled"

            try:
                if cancelled():
                    raise InterruptedError
                result = self.execute(job["payload"]["prompt"], cancelled, self.timeout)
                status = "completed"
            except InterruptedError:
                status, result = "cancelled", {"error": "Execution interrupted"}
            except TimeoutError:
                status, result = "timed-out", {"error": "Execution deadline exceeded"}
            except Exception:
                status, result = "failed", {"error": "Inference backend failed"}
            self.queue.finish_inference(job_id, status, result)


class LocalLlama:
    def __init__(self, backend, model, digest, threads, memory_mib, lock_fd):
        if sys.platform not in ("linux", "win32"):
            raise ValueError("No process supervisor for this platform")
        self.backend = Path(backend).resolve(strict=True)
        self.model = Path(model).resolve(strict=True)
        if self.model.stat().st_size != inference.MODEL_SIZE:
            raise ValueError("Unexpected pinned model size")
        inference.verified_digest(self.model, inference.MODEL_SHA256)
        inference.verified_digest(self.backend, digest.lower())
        self.threads = threads
        self.memory_mib = memory_mib
        self.lock_fd = lock_fd

    def __call__(self, prompt, cancelled, timeout):
        command = [sys.executable, "-m", "community_grid.worker", str(self.backend), str(self.model),
                   str(self.threads), str(self.memory_mib), str(timeout), str(os.getpid())]
        windows = sys.platform == "win32"
        job = None
        process = None
        assigned = False
        deadline = time.monotonic() + timeout
        try:
            if windows:
                from .windows_job import WindowsJob
                job = WindowsJob(self.memory_mib * 1024**2, self.threads * timeout)
            options = {} if windows else {"start_new_session": True, "pass_fds": (self.lock_fd,)}
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **options)
            if windows:
                job.assign(process)
                assigned = True
            payload = json.dumps(prompt).encode()
            while True:
                if cancelled():
                    raise InterruptedError
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    output, _ = process.communicate(payload, timeout=min(remaining, 0.15))
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if process.returncode:
                raise RuntimeError("Inference child exited unsuccessfully")
            return json.loads(output)
        finally:
            if job:
                job.close()
            if process:
                if windows:
                    if not assigned and process.poll() is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
                process.stdin.close()
                process.stdout.close()


def child(backend, model, threads, memory_mib, timeout, parent):
    if sys.platform == "win32":
        # The parent sends stdin only after assignment. EOF or invalid JSON
        # exits without starting any backend if the owner dies before that.
        prompt = json.load(sys.stdin)
        execute_child(backend, model, threads, prompt)
        return
    import ctypes
    import resource

    # If the dashboard dies, keep the inherited owner lock until the bounded
    # child has killed and reaped its backend.
    def interrupted(signum, frame):
        raise InterruptedError("Worker owner exited")

    signal.signal(signal.SIGTERM, interrupted)
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) or os.getppid() != int(parent):
        raise RuntimeError("Cannot link inference child lifetime to its worker")
    memory = int(memory_mib) * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    cpu = int(threads) * int(timeout)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    execute_child(backend, model, threads, json.load(sys.stdin))


def execute_child(backend, model, threads, prompt):
    version = subprocess.run([backend, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=10, check=True).stdout.decode()
    if inference.BACKEND_REVISION[:7] not in version:
        raise ValueError("Unexpected llama.cpp version")
    print(json.dumps(inference.run_threads(backend, model, int(threads), 1, False, prompt)))


if __name__ == "__main__":
    child(*sys.argv[1:])
