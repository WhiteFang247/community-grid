"""Measure Linux process high-water RSS for the two M2 reference operations."""
import argparse
import hashlib
import json
import platform
import resource
import statistics
import subprocess
import sys
import time

from community_grid.graph import OPERATIONS, encode

def child(mode, rounds, size):
    data = (" seed \t library " * ((size + 15) // 16))[:size]
    start = time.perf_counter()
    if mode == "reference":
        normalize = OPERATIONS["text.normalize"][2]
        sha256 = OPERATIONS["text.sha256"][2]
        reference = hashlib.sha256(" ".join(data.split()).encode()).hexdigest()
        for _ in range(rounds):
            value = sha256(normalize(data))
            if value != reference:
                raise ValueError("Reference operation mismatch")
            encode(value)
    elapsed = time.perf_counter() - start
    print(json.dumps({"peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      "seconds": elapsed}))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=("baseline", "reference"))
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--bytes", type=int, default=32000)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Linux is required for ru_maxrss in KiB")
    if not 1 <= args.samples <= 30 or not 1 <= args.rounds <= 2000 or not 16 <= args.bytes <= 32000:
        parser.error("sample, round, or payload count exceeds benchmark bounds")
    if args.child:
        child(args.child, args.rounds, args.bytes)
        return
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "pairs_per_sample": args.rounds, "max_input_chars": args.bytes}
    for mode in ("baseline", "reference"):
        samples = []
        for _ in range(args.samples):
            start = time.perf_counter()
            sample = json.loads(subprocess.check_output(
                [sys.executable, __file__, "--child", mode, "--rounds", str(args.rounds),
                 "--bytes", str(args.bytes)], timeout=20))
            sample["process_wall_seconds"] = time.perf_counter() - start
            samples.append(sample)
        report[mode] = {"samples": samples,
                        "max_peak_rss_kib": max(s["peak_rss_kib"] for s in samples),
                        "median_peak_rss_kib": statistics.median(s["peak_rss_kib"] for s in samples),
                        "median_seconds": statistics.median(s["seconds"] for s in samples),
                        "median_process_wall_seconds": statistics.median(
                            s["process_wall_seconds"] for s in samples)}
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
