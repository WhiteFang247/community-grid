# Community Grid OS: Local-first dual-mode execution

Status: approved architecture; implementation pending.

## Principle
Every device is independently usable, with local inference, local tools, and an offline-capable recovery interface. Joining a cluster expands capacity and services but never becomes a boot or login dependency. Cluster loss must not disable local AI functionality.

## Modes
- **Standalone:** run supported local models and jobs, inspect hardware, manage local tools with permission broker; no coordinator required.
- **Cluster:** authenticated peers advertise capabilities and resource headroom; coordinator routes independent jobs to appropriate nodes and can optionally use supported distributed single-model inference when measured performance warrants it.
- **Fallback:** upon peer/coordinator loss, stop scheduling new remote jobs, mark in-flight jobs uncertain, retry only idempotent jobs, and continue locally within capacity. Do not promise transparent resumption of arbitrary distributed inference state.

## Scheduler design
1. Classify job: independent batch, independent inference request, or tightly coupled distributed inference.
2. Measure CPU/GPU suitability, memory headroom, model availability, expected completion time, network transfer cost, node health and trust.
3. Prefer data/model locality and minimize intermediate transfers.
4. Support explicit per-node reservations so background cluster work does not starve interactive local use.
5. Use per-job IDs, leases, cancellation, bounded retries, deduplication and result verification; never assume exactly-once execution.
6. Keep cluster membership optional and revocable.

## Communication design
- Discovery via explicitly approved LAN discovery or configured peer addresses; no automatic trust.
- Persistent authenticated encrypted connections with protocol/version negotiation and backpressure.
- Compact versioned capability and heartbeat messages, separate control plane from bulk model/data transfers.
- Chunked/resumable content-addressed artifact transfers with integrity checks and cache reuse.
- Collect per-peer RTT, jitter, sustained bandwidth, connection setup, serialization overhead and failure rates.
- Benchmark transport alternatives under real workloads before introducing complexity (e.g. QUIC vs TLS/TCP); don't assume protocol changes beat data-locality improvements.
- Never expose experimental llama.cpp RPC directly to untrusted networks.

## Service boundaries
- hardware profiler
- local inference adapter
- node agent + enrollment
- peer transport
- scheduler/job queue
- model/artifact cache
- permission broker
- assistant interface
- optional coordinator

## Validation matrix
- Single device with no network
- Two x86 machines with independent jobs
- Node disconnection during an idempotent job
- Node disconnection during non-idempotent work
- Different architectures and unavailable model/backend
- Constrained RAM and interactive workload competing with background tasks
- Latency/bandwidth sweep with measured useful throughput and overhead
- Unauthorized node enrollment and remote execution attempts
- Same model local vs distributed inference where supported

## Near-term build order
1. Versioned node capability schema and resource measurements.
2. Local-only job execution and persistent job metadata.
3. Authenticated peer registration and heartbeat.
4. Simple independent-job scheduler with measurements.
5. Inference integration and per-node model cache.
6. Optional distributed model partitioning based on benchmark evidence.

This decision supersedes any assumption that single-model partitioning is the default cluster workload.
