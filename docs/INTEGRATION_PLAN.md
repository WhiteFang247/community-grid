# Integration plan: Community Grid + PrivateLM concepts

## Preserve existing work
The previous local `community-grid` prototype is not present in this GitHub repository yet. Inventory its scheduler, worker protocols, tests, and deployment files before moving or replacing anything. Keep a migration branch and test against a local two-device setup.

## UI design (distinct Community Grid identity)
- Dashboard: available capacity, active jobs, participating devices, health.
- Devices: discover, QR pair, approve, inspect capabilities, set resource limits, disconnect.
- Workloads: AI, general compute, rendering/research, gaming experiments.
- Activity: jobs, throughput, failures, contribution history.
- Settings: privacy, energy, network, security, opt-in.

Use PrivateLM's low-friction discovery and pairing as UX inspiration, not a direct copy of its chat-first interface. Build original icons, copy, colors, layouts, and branding.

## Protocol architecture
- Coordinator: authenticated enrollment, heartbeats, capability registry, job scheduling.
- General worker: opt-in sandboxed, bounded tasks with cancellation.
- LLM adapter: evaluate upstream llama.cpp RPC compatibility and licensing; do not assume cross-version interoperability.
- Platform adapters: desktop, Android, iOS where OS background limits permit.
- Local first: LAN discovery and user approval; remote networking later.

## Important constraints
- llama.cpp RPC workers are not automatically secure. Wrap them in trusted networking and an authenticated control plane.
- RAM and GPU are not universally pooled across heterogeneous hardware.
- Remote gaming support begins with streaming and workload-specific experiments, not a claim that arbitrary games gain FPS through Wi-Fi.
- Measure bandwidth, latency, power, thermal behavior, and scheduling overhead.

## Attribution
PrivateLM: https://github.com/orailnoor/cross-platform-llm-client (MIT). Preserve original license and copyright notices when code is incorporated. Verify license status for each vendored dependency and native library.

## Acceptance criteria for first prototype
1. Pair two devices with explicit approval.
2. Display reliable hardware capabilities and contribution limits.
3. Submit and cancel a sandboxed compute task.
4. Run a measured, separately gated LLM RPC experiment.
5. Record latency, throughput, and errors.
