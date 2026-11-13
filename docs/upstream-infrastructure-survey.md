# Community Grid OS — Upstream Infrastructure Survey (2026-11-13)

Status: research and integration plan only. No third-party source vendored, packages installed, or services deployed.

## Guiding rule
Reuse upstream packages and stable APIs before copying source. Pin versions and hashes; retain upstream licenses/notices; audit dependencies, redistribution obligations and security before packaging. A link in this document does not authorize redistribution.

## Candidate matrix

| Project | Upstream | Proposed role | Approach | Caution |
|---|---|---|---|---|
| Debian / live-build | https://live-team.pages.debian.net/live-manual/html/live-manual.en.html | Debian-based bootable image, apt package management | Debian packages + live-build configuration | Do not vendor Debian source wholesale; track release and package provenance |
| Proxmox pve-cluster | https://github.com/proxmox/pve-cluster | Study cluster membership, configuration replication and UI patterns | Architectural reference, not dependency in v0.1 | Corosync assumes low-latency reliable networks; Proxmox dependencies and licensing need review |
| BOINC | https://github.com/BOINC/boinc | Independent batch jobs, heterogeneous/untrusted workers, resource accounting | Study scheduling/client protocols; evaluate separate adapter later | Throughput computing is not synchronous per-token distributed LLM inference |
| llama.cpp | https://github.com/ggml-org/llama.cpp | CPU/GPU quantized LLM inference, local serving, RPC experiments | External pinned build/package; integrate APIs | RPC upstream calls it proof-of-concept and insecure on exposed networks; isolate behind authenticated transport |
| exo (current) | https://github.com/exo-explore/exo | Model partitioning, topology measurement, device discovery | Study algorithm/design, optional integration spike | Current focus on MLX/Apple hardware; validate x86/ARM compatibility |
| ex-exo (archived) | https://github.com/exo-explore/ex-exo | Historical heterogeneous inference architecture | Reference only | Archived implementation; do not assume maintained |
| Mesa V3DV | https://docs.mesa3d.org/drivers/v3d.html | Raspberry Pi GPU driver and compute research | Use distro driver, test pinned versions | Vulkan capability does not guarantee useful AI throughput |
| XDC 2026 V3DV talk | https://indico.freedesktop.org/event/12/contributions/529/ | Pi 5 LLM Vulkan feasibility evidence | Reproduce published experiments | Memory ceiling, fp16 and shader limitations |

## Layered target architecture
1. Debian packages/systemd/udev/DRM/Mesa: host OS and drivers.
2. Community Grid agent: capability discovery, authenticated enrollment, heartbeats, metrics.
3. Workload control plane: scheduling, resource reservations, placement, model catalog.
4. Inference adapters: llama.cpp local server; experimental RPC behind private transport; future exo adapter if compatible.
5. Independent-job queue: BOINC-inspired throughput scheduler for embeddings, evaluation, batch processing and independent requests.
6. Built-in AI assistant: local model selector, installer planner, permission broker, audited tools and desktop UI.
7. Distro build: reproducible live-build configs, Debian packages, signed update channels.

## Decision record
- ADOPT Debian package management and live-build for future OS images; do not import Debian's entire source.
- ADOPT llama.cpp as first inference backend, pinned as an external dependency.
- STUDY Proxmox/Corosync design; defer adoption because our nodes may be intermittent and WAN-connected.
- STUDY BOINC scheduling; avoid treating its batch-job scheduler as a substitute for low-latency model partitioning.
- STUDY exo's partitioning and discovery; benchmark on actual x86/ARM devices before adopting.
- EXPERIMENT with Pi 5 Vulkan/V3DV only after verifying compatible Mesa driver, model support and measurements.
- Keep existing Community Grid experiments; inventory and import only after source review.

## Implementation sequence
1. Inventory current local Community Grid source and experimental logs; avoid overwriting it.
2. Add upstream manifest with pinned versions, license audit, checksum/source and supported architectures.
3. Extend hardware capability discovery: free RAM, CPU flags, driver/backend checks, GPU compute eligibility.
4. Implement read-only installer/model selection plan; benchmark candidates without automatic installs.
5. Add authenticated agent enrollment and persisted registry with revocation.
6. Integrate llama.cpp backend and test local inference before RPC.
7. Benchmark Lenovo-only, Dell-only, and distributed inference; compare latency, tokens/sec and memory.
8. Test Raspberry Pi CPU and Vulkan separately if hardware is available.
9. Package runtime via Debian .deb; then build disposable live ISO.

## Security and acceptance
- No arbitrary remote shell by default; no unauthenticated public RPC.
- No root privileges for model processes; privileged actions require explicit approval.
- No silent network exposure, downloads, or installation.
- Distinguish aggregate batch throughput from single-request inference performance.
- Preserve upstream notices/licenses and comply with applicable copyleft conditions.
- CI should test x86 and ARM code paths with mocks; real hardware tests must report measured results.

## Research references
- Debian live-build manual: https://live-team.pages.debian.net/live-manual/html/live-manual/customizing-package-installation.en.html
- Proxmox cluster documentation: https://github.com/proxmox/pve-docs/blob/master/pvecm.adoc
- BOINC overview: https://github.com/BOINC/boinc/wiki/BOINC-overview
- BOINC source map: https://github.com/BOINC/boinc/wiki/Source-code-map
- llama.cpp RPC: https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md
- exo current: https://github.com/exo-explore/exo
- XDC 2026 Raspberry Pi Vulkan LLM presentation: https://indico.freedesktop.org/event/12/contributions/529/
