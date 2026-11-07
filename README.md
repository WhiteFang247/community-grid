# Community Grid

**One network. Many devices. Shared computing power.**

Community Grid is an open-source project exploring how computers, phones, and other devices can contribute available computing resources to a coordinated network. Its long-term goals include distributed AI inference, sandboxed research and rendering jobs, and practical gaming-related experiments.

> **Status:** Early prototype / architecture stage. General-purpose GPU pooling and accelerating arbitrary PC games are **not** implemented. Existing games cannot simply borrow remote GPUs or RAM as if they were local hardware.

## Product experience

- Discover devices on a local network and pair using QR codes or an approval flow.
- View device availability, CPU/GPU/RAM capabilities, temperatures, and contribution limits.
- Choose whether a device contributes resources, requests compute, or coordinates jobs.
- Submit workloads, monitor progress, and revoke access.
- Start on trusted local networks; expand to internet-connected communities only after authentication, encryption, isolation, and consent are in place.

## Workload roadmap

| Workload | Approach | Stage |
| --- | --- | --- |
| AI / LLM inference | Explore llama.cpp RPC and compatible GGUF model-layer distribution | Research / integration |
| General CPU jobs | Sandboxed independent tasks dispatched by a coordinator | Planned |
| Rendering and research | Partitionable batch jobs with verifiable results | Planned |
| Gaming | Evaluate streaming, remote rendering, and game-adjacent jobs; measure latency and compatibility | Research |

## Architecture direction

```text
Apps (desktop / mobile / web)
          |
Device pairing + authenticated control plane
          |
Coordinator / capability registry / scheduler
          |
Worker adapters: general jobs | llama.cpp RPC | future render adapters
          |
Opt-in devices: PCs | Android | iOS (where permitted) | servers
```

The coordinator schedules jobs and enforces permissions; the workload adapters implement specific kinds of compute. Devices must opt in, and job execution must be isolated from personal files and accounts.

## Relationship to PrivateLM

[PrivateLM](https://github.com/orailnoor/cross-platform-llm-client) by orailnoor is an **independent MIT-licensed project** and a reference for intuitive device pairing and llama.cpp RPC-based model distribution. Community Grid will develop distinct branding, navigation, and general-compute workflows. Any source copied or adapted from PrivateLM must retain its applicable copyright and license notices and be tracked in third-party attribution documentation. This repository does not yet contain PrivateLM source code.

## Development milestones

1. Preserve and audit the existing Community Grid prototype before migration.
2. Implement a unified device inventory, pairing, and resource dashboard.
3. Test local-network AI inference with compatible RPC workers.
4. Introduce sandboxed general-purpose tasks and a capability-aware scheduler.
5. Benchmark gaming-related approaches without promising transparent remote GPU acceleration.

## Security principles

Explicit device enrollment, mutual authentication, encrypted control traffic, user-set resource budgets, revocable permissions, sandboxed tasks, and no arbitrary remote desktop access. Upstream llama.cpp RPC implementations may lack authentication; never expose unprotected RPC workers to the public internet.

## License

Community Grid is licensed under [Apache License 2.0](LICENSE). Third-party components retain their respective licenses.
