# Community Grid OS Runtime v0.1 (foundation)

This is a **local-only prototype**, not yet a distributed inference system or a bootable OS. It provides hardware discovery and an in-memory node registry, with no network listener, persistence, authentication, or scheduler.

## Requirements

Python 3.10+ on Linux (Debian preferred). Hardware discovery also returns partial information on other platforms. No third-party Python packages are required.

## Run

From the repository root:

```bash
PYTHONPATH=runtime python3 -m community_grid.hardware
PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v
```

Optional: install `vulkan-tools` to discover Vulkan device names. A reported Vulkan device is **not** proof that it can accelerate LLM inference; software renderers may appear and all devices are marked unverified.

## Integration plan

1. Recover and inventory existing local Community Grid coordinator, inference worker, experiments and API code before migration.
2. Add authenticated node enrollment and periodic heartbeats with persistent storage.
3. Benchmark actual CPU and accelerator backends and persist performance measurements.
4. Integrate llama.cpp RPC only after confirming versions, topology, and security constraints.
5. Expose an OpenAI-compatible endpoint for Odysseus.

No automatic deployment, firewall modifications, remote code execution, or untrusted worker enrollment are included.
