# Community Grid OS — Code review and upstream integration findings

Scope: repository branch `feature/community-grid-os` and Debian live-build, BOINC, llama.cpp upstream documentation. This review does **not** claim to have inspected private/unpublished local Lenovo or Dell experiments.

## Verified baseline
- GitHub Actions runtime tests: passing as of initial build.
- First experimental Debian live ISO workflow: GitHub Actions reports success, **not a verified Dell boot**.
- Read-only localhost dashboard and local job queue exist.
- No authenticated peer transport, model runtime, local chat assistant, or real distributed scheduler implemented yet.

## Priority findings
1. **First-boot observability**: Add allowlisted, read-only diagnostics for network interfaces, PCI/USB, block devices, and service status; never collect secrets automatically. Added `diagnostics.py`.
2. **Memory scheduling**: Existing model recommender uses total RAM; should consider available RAM and workload headroom, and avoid claiming a model is supported until benchmarked.
3. **Job concurrency**: Original SQLite queue read status and updated it without a conditional claim, permitting concurrent runners to execute one job twice. Require atomic `UPDATE ... WHERE status='queued'` before invoking handler.
4. **Cluster registry concurrency**: Original `list_nodes()` acquired lock only while collecting IDs, allowing entries to change before reading. Snapshot under lock.
5. **Security**: Do not expose llama.cpp RPC to untrusted networks. Keep networked model execution behind explicit enrollment, encryption, authorization and sandboxing.
6. **Installer**: Debian live-build supports includes and chroot hooks, making our approach reasonable. The successful ISO build does not validate Dell BIOS, networking, desktop startup or SSH readiness.
7. **Codex supervision**: Direct Ethernet via Lenovo USB-A gigabit adapter is appropriate. Add SSH to the live image but keep remote login disabled until explicitly provisioned with a trusted key. Avoid default passwords.

## Upstream patterns to adopt
- BOINC workunit/result distinction, platform-aware scheduling, deadlines, and resource shares. Our first job schema should track execution attempts and capabilities separately from logical jobs.
- Debian live-build chroot includes/hooks, distro packages and systemd service hardening.
- llama.cpp local serving for first inference; never treat experimental RPC as safe default.
- Proxmox quorum-based coordination is a reference only; we need local-first behavior without quorum dependence.

## Validation gates
- Tests for concurrent job claims and failure handling.
- Build live ISO and publish checksums.
- VM boot with dashboard/service checks.
- Dell live USB boot and diagnostics export.
- Only then enable optional SSH enrollment and direct-cable tests.
- Benchmark latency, jitter, bandwidth, transfer cost and independent-job throughput before adding distributed model partitioning.

## References
- https://live-team.pages.debian.net/live-manual/html/live-manual.en.html
- https://github.com/BOINC/boinc/wiki/BasicConcepts
- https://github.com/ggml-org/llama.cpp/blob/master/SECURITY.md
- https://github.com/ggml-org/llama.cpp/security/advisories/GHSA-j8rj-fmpv-wcxw
