"""Run the real worker engine on Linux and report service and child RSS separately."""
import json
from pathlib import Path
import platform
import statistics
import tempfile
import uuid

from community_grid.graph import digest, encode
from community_grid.worker_v2 import Worker


def main():
    samples = []
    with tempfile.TemporaryDirectory() as directory:
        worker = Worker(Path(directory) / "worker.db", "benchmark", "cpu", "test-boot")
        value = (" seed \t library " * 2000)[:32000]
        with worker.executor() as lock:
            for op in ("text.normalize", "text.sha256"):
                for _ in range(10):
                    lease = dict(graph="benchmark", task=op, node="cpu", worker_boot_id="test-boot",
                                 coordinator_boot_id="test-coordinator", generation=1,
                                 attempt=str(uuid.uuid4()), deadline=10, budget_seconds=5,
                                 cpu=1, ram_mib=128, operation=op, input_digest=digest(encode(value)))
                    lease["request_digest"] = digest(encode(lease))
                    request = {"version": 2, "lease": lease}
                    worker.request(dict(request, action="submit", value=value))
                    if not worker.run_one(lock):
                        raise RuntimeError("Benchmark task was not claimed")
                    reply = worker.request(dict(request, action="status"))
                    if reply["status"] != "completed":
                        raise RuntimeError(reply)
                    samples.append({"operation": op, **{k: reply[k] for k in (
                        "service_peak_rss_kib", "child_peak_rss_kib", "elapsed_seconds")}})
    print(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                      "memory_limit_mib": 128, "input_bytes": len(value.encode()),
                      "max_service_rss_kib": max(s["service_peak_rss_kib"] for s in samples),
                      "max_child_rss_kib": max(s["child_peak_rss_kib"] for s in samples),
                      "median_elapsed_seconds": statistics.median(s["elapsed_seconds"] for s in samples),
                      "samples": samples}, indent=2))


if __name__ == "__main__":
    main()
