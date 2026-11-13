# Community Grid OS (experimental)

A Debian-based, local-first AI workstation and optional heterogeneous compute cluster.

> **Development preview, not a production OS.** The GitHub repository does not yet include the earlier Community Grid distributed inference experiments from the Lenovo/Dell machines. Current code is a standalone runtime prototype with a read-only local dashboard, hardware discovery, heuristic model recommendations, a SQLite local job primitive, and an **unverified** Debian live ISO build recipe. It does not currently run an LLM, chat agent, or distributed workloads.

## Quick start on Debian / Linux

Requires Python 3.10+.

```bash
PYTHONPATH=runtime python3 -m community_grid.cli hardware
PYTHONPATH=runtime python3 -m community_grid.cli recommend
PYTHONPATH=runtime python3 -m community_grid.server
```

Open http://127.0.0.1:8765 on the same machine. The API binds to loopback only and refuses POST requests.

```bash
PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v
```

## Experimental live USB for Dell OptiPlex 990 SFF

The repository contains a Debian Trixie amd64 XFCE live image recipe and a manually triggered GitHub Actions ISO build workflow.

1. Open [Build Community Grid OS live ISO](https://github.com/WhiteFang247/community-grid/actions/workflows/build-live-iso.yml) and choose **Run workflow** on branch `feature/community-grid-os`.
2. If the build succeeds, download the `community-grid-os-amd64-live` artifact and verify the SHA256 checksum.
3. Follow [Dell live boot guide](docs/dell-live-boot.md) before flashing a **spare USB**. Flashing erases the selected USB device.
4. Boot from the Dell's F12 menu without modifying its internal disk.

This ISO has not yet been built and hardware-tested. The build workflow might require corrections based on its first run.

## Current components
- `runtime/community_grid/hardware.py`: host CPU/OS/RAM and optional Vulkan device names.
- `runtime/community_grid/models.py`: heuristic model recommendation only; no download.
- `runtime/community_grid/jobs.py`: SQLite-backed, explicitly allowed local job primitive; not a cluster queue.
- `runtime/community_grid/server.py`: localhost-only preview dashboard and read-only API.
- `distro/`: Debian live-build recipe, XFCE desktop launcher, constrained dashboard systemd service.
- `docs/`: architectural specifications and upstream research.

## Planned work
- Recover previous distributed Community Grid code and benchmark evidence.
- Improve model catalog with verified metadata, hashes, licenses, and benchmarks.
- Add reproducible llama.cpp installation, model downloads with consent, local chat UI.
- Build permission-controlled OS tools and secure peer enrollment.
- Implement resource-aware independent-job scheduling and optional measured distributed inference.
- Validate the live ISO in a VM and on the Dell; then evaluate persistent installation.

## Safety
No automatic root-level AI commands, no network-exposed RPC, no silent downloads or deployments. Do not assume the dashboard's heuristic model recommendation implies model compatibility or useful speed.

License: see [LICENSE](LICENSE).
