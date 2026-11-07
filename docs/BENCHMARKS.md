# Recovered Codex prototype results (November 2–4, 2026)

These are historical observations, **not** fresh v0.2 measurements.

- Dell host: `pronmetheus`; 4 logical CPUs, 15.58 GB RAM.
- Single worker executed `sum_squares` for the half-open range `[1, 100000)`.
- Verified result: `333328333350000`.
- Timestamp-derived compute duration: approximately 8.4 ms (not an end-to-end network benchmark).
- Tailscale Dell `100.95.40.108`; Lenovo `100.106.72.97`.
- Coordinator port `8765`; Tailscale HTTPS serve `:8766` to localhost `:8765`.
- 401 without authentication / 200 with authentication were observed.
- SSH/SCP worker and token transfers were confirmed.
- Lenovo Windows Python 3.14.7 with `httpx` and `psutil`.

The original v1 implementation had in-memory job and node state, a 15-second heartbeat timeout, 60-second lease, and resource gating at >80% CPU or <0.5 GB available RAM.

## Next measurements
Run tests on both machines simultaneously. Record wall-clock queue latency, execution time, network overhead, correctness, and energy draw. Compare one worker with two workers on partitioned workloads; this initial version does **not** implement workload partitioning.

## PrivateLM integration
The mobile UI, QR enrollment, and llama.cpp RPC worker are a **planned** second stage, not functionality present in this reconstruction. Evaluate protocol version compatibility, mobile foreground/background limits, network throughput, and unauthenticated upstream RPC before enabling it.
