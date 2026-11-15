# CGOS 0.1-M2 — Two independently booted nodes

Status: design for review. The exit gate is a verified DAG with actual CPU work
on two independently booted CGOS guests, including bounded recovery after a
worker interruption. Each guest has its own kernel, boot identity, service
journal, private state and enrollment identity. M1 local mode remains the default.

## Reuse and implementation boundary

Phase 1A's graph.py validates typed operations and DAG dependencies, persists
assignments and reservations in SQLite, verifies result bytes independently,
and fences late or duplicate publication by attempt ID. Phase 1B's physical.py
provides bounded probe/submit/status/cancel messages, SSH transport, and replayable
worker results and cancellation tombstones. Preserve those semantics. The M1
native daemon currently only admits local placement and performs synchronous
ticks in its Unix socket loop. The earlier inference coordinator is useful for
heartbeat and logging patterns but does not become the graph scheduler.

1. Add an opt-in node registry and scheduler adapter to the native runtime.
   Keep the Unix socket as the operator interface. Use one scheduling owner
   and a bounded transport executor so SSH waits cannot block CLI traffic.
   Keep placement and accepted result publication transactionally serialized.
2. Package the Phase 1B relay as a separate opt-in worker service. Reuse
   its idempotent request and terminal response records. Add versioned
   capability reports, durable attempt events, and explicit process/resource
   limits before advertising more than one slot.
3. Add cross-node acceptance CI and fault injection. Keep changes on a separate
   implementation branch and build an independently identified M2 artifact.

## Roles and protocol

Node A runs the coordinator and a local worker; node B runs the remote worker.
Both use the same allowlisted CPU operation registry. Only the coordinator
admits graphs, schedules dependency-ready units, verifies artifacts, records
attempts and commits results. Workers execute only validated operations and
report attempt IDs, durations and results; they cannot publish graph state.

Enroll explicitly through an administrator-owned manifest that binds a stable
node ID to an endpoint, account and independently provisioned Ed25519 host pin.
Generate a throwaway coordinator key per CI run and pass only its public key to
the worker. Use strict host checking, a fixed forced command, an unprivileged
worker account, source restriction, no forwarding, no TTY or subsystems, and
immutable code and trust configuration. Validate the effective SSH policy and
exercise access-denial checks on a stock Linux SSH daemon before graph tests.
No trust on first use or automatic enrollment. Removing the manifest entry
blocks scheduling; revocation also removes the worker-side authorized key.

Use authenticated probes for discovery after enrollment. Report protocol and
operation versions, architecture, CPU slots, memory budget and boot instance.
Treat capabilities as claims, clamp them to operator limits, and mark a worker
offline after a bounded missed-probe window. Cache probes so the scheduler
does not create one SSH session for every eligible task. Start with one slot
per node, a deterministic tie-break, and memory and CPU reservations. Enforce
caps with service/cgroup limits or a bounded launcher. Network scope is a
private guest-only link with just the worker SSH listener; no public bridge,
guest Internet, host forwarding, or network-accessible operator API.

Use bounded canonical JSON results initially (64 KiB wire limit, with headroom
for framing). A manifest binds type, size, SHA-256 and attempt identity.
Verify size, digest and type before storing under the digest. Reject stale
attempts before publication. Recompute the small reference operations with
an independent implementation to check correctness in addition to integrity.
The downstream unit receives only verified parent value bytes. Larger artifacts
and external stores are deferred. CPU execution is versioned by operation
and backend. Future GPU/AI backends may report device memory, supported
operations and determinism requirements; they are outside this milestone.

## Recovery rules

Persist the assignment before dispatch and reuse the same UUID when the
acknowledgment is lost. Reconcile status before creating a new attempt.
Renew no lease after a worker is offline; use bounded RPC, attempt and graph
deadlines. If worker state is gone or an attempt expires, fence that attempt
before retrying with a fresh UUID on an eligible healthy node. Keep a lost
worker's capacity quarantined until reconciliation or a confirmed new boot
instance. A pure operation may execute more than once; only the current
attempt may publish. Reject altered results, stale responses and conflicting
duplicate requests. Reject operations with external side effects in M2.

## Hosted acceptance design

Use two QEMU guests on one disposable GitHub-hosted Ubuntu runner, with a
guest-only socket network and no TAP/physical disk attachments or port
forwarding. Pin a specific build run, artifact digest and source commit.
Reuse the M1 serial-console harness, KVM detection and TCG fallback. Use
separate boot processes, read-only copies of the same M2 live ISO, private
temporary state and per-guest console logs. Stage one-run public trust
material out of band with read-only virtual media. Generate and retain private
keys only in the isolated environments that use them. Publish sanitized
evidence even on failure; terminate both VMs on deadline.

- **Boot and enroll:** verify separate boot IDs, live mounts, units and the
  worker pin; show the coordinator's registry and per-node capabilities.
  Assert unknown keys, incorrect host pins, and non-relay SSH requests fail.
- **Healthy run:** A normalizes input to `seed library`; B hashes it; A consumes B's
  verified output in a third hash task. Force placements with existing
  node allowlists. Independent reference code checks each value and canonical
  JSON digest. Correlate both guests' task logs with coordinator events using
  graph ID, task ID, attempt UUID, authenticated node and boot ID.
- **Interruption run:** first complete actual work on both nodes. Then disrupt
  B's experimental relay immediately after durable dispatch of a later unit,
  using a test-only barrier. For one scenario drop the reply after worker
  commit, restore B and require reconciliation under the same UUID. For
  another keep B offline beyond the attempt deadline, permit fallback on A,
  then replay B's late response and assert it cannot publish. Both graphs
  must reach the correct final state, with exactly one accepted result per
  unit and a complete placement and recovery trace.
- **Evidence:** retain per-guest kernel and service logs, enrolled capabilities,
  normalized events, artifact manifests, independent verification and timing
  summaries. A boot, access, placement, digest, recovery or cleanup failure
  fails the run. This is virtual multi-node evidence only.

## Initial measurement targets

Run one warm-up plus ten healthy graphs and five of each fault scenario on the
same runner. Use two vCPUs, 1.5 GiB RAM per guest, one slot each, and record QEMU
version, acceleration and VM configuration. Report median/p95 and raw samples;
include a same-run local-only baseline. Treat TCG and KVM data separately.

| Metric | Initial acceptance bound |
| --- | --- |
| Each guest to live userspace and serial readiness | <= 240 s |
| Healthy graph completion | p95 <= 20 s after admission |
| Dispatch round trip and queue delay | report median/p95; each RPC <= 3 s |
| Loss detection after last good 2 s heartbeat | <= 10 s |
| Fault graph completion | <= 45 s after interruption; hard graph deadline 60 s |
| Result integrity and accepted publication | 100% of reference digests match; 0 stale or duplicate publications |
| Resource limits | 1 reserved slot/node; 0 leaked reservations after reconciliation |

Use monotonic spans on each node for queue, transport, compute and startup.
Measure detection and recovery end to end from the coordinator's clock.
Record artifact bytes and operations/second for each run; avoid inferring
speedup from these small tasks. Preserve raw samples and revisit targets only
with documented evidence. Physical execution and full-reboot persistence
remain separate decisions.
