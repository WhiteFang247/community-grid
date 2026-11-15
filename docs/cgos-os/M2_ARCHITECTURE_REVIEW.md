# CGOS 0.1-M2 architecture review

Decision for implementation review: extend the Phase 1A scheduler and
Phase 1B worker behind the native Unix socket. Keep one coordinator,
one slot per node, pure CPU operations, and two independently booted
live guests. Keep M1 local mode as the default. This review authorizes
no physical deployment and introduces no runtime changes.

## What exists and what changes

| Component | Review finding and disposition |
| --- | --- |
| Phase 1A graph validation and operation registry | Reuse typed inputs, DAG validation, canonical JSON and the existing two pure operations unchanged. Keep the scheduler as the sole graph authority. |
| GraphRuntime SQLite state | Retain transaction boundaries, persisted assignment-before-dispatch, dependency gating, deterministic placement and guarded publication. Extend state with generation, authenticated worker instance, immutable input digest, explicit lease and independently tracked quarantine. Current expiry on disconnect can retain cleanup without enabling a replacement attempt. |
| Phase 1B worker | Reuse the bounded JSON transport, fixed SSH relay and durable terminal replies/tombstones. Extend probe, request and reply schemas. Current submit calculates inside one transaction and only acknowledges after completion. Represent durable admission separately and add bounded execution, running status and recovery semantics. |
| Native M1 API and service | Preserve the local socket and CLI behavior. Add opt-in registry, authenticated peer adapters and reconciled scheduling; move blocking remote calls off the Unix socket loop. Preserve one SQLite state writer. |
| Inference coordinator | Borrow heartbeat and structured log patterns only. It schedules independent inference jobs and must not become a second authority for graph state. |
| CI | Reuse M1 artifact verification, disposable QEMU, serial and QMP evidence. Create a distinct M2 image and workflow. The M1 artifact and source refs remain historical inputs. |

## Lease and idempotency contract

- The coordinator owns each lease and issues it to exactly one enrolled
  worker boot instance. A persisted assignment contains graph ID, task ID,
  a strictly increasing per-task generation, attempt UUID, node ID, boot
  ID, canonical request digest, resources and the coordinator expiry.
  The fence is the generation and attempt UUID together. Persist the
  reservation and assignment in the same transaction before sending.
- The coordinator is the sole authority for expiry. Use a monotonic clock
  with the coordinator boot ID. Same-boot process restart retains the
  deadline. A changed or unknown coordinator boot ID fences outstanding
  leases before reconciliation and prevents uncertain leases from being
  revived. Workers enforce local monotonic execution budgets beginning
  at first admission; duplicate submissions never extend them. Worker
  timestamps are diagnostic and are not used for cross-node expiry.
  Use fixed leases for M2 with no renewal.
- The worker idempotency key is coordinator identity plus attempt UUID.
  Bind it to a canonical digest over the semantic request, including graph,
  task, fence, destination, input, operation and fixed execution budget.
  Identical repeats return existing queued/running/terminal state.
  Conflicting repeats fail closed. Write admission before acknowledging.
  A cancel for an unobserved attempt creates a tombstone that rejects a
  later submit. Cache terminal replies and tombstones for the full run.
- An uncertain RPC is neither success nor failure. Query the same attempt
  first. Resubmit that same request only if the authenticated worker still
  reports missing, no prior admission is known, the boot ID matches, and
  the lease remains valid. If admission was known and state is missing,
  fence as lost. On expiry or confirmed loss, atomically retire the old
  fence and allow a new generation elsewhere if attempt and graph budgets
  permit. Quarantine the old node slot separately until the same instance
  confirms terminal/missing state or an authenticated new instance
  establishes that the old executor is gone. Never schedule against both
  reservations as if they were free.
- Repeat execution of a pure operation is possible. The guarantee is one
  accepted publication per logical task, not exactly-once execution.

## Result commit and dependency contract

Accept over the authenticated channel assigned to that lease. In one
coordinator transaction, verify the graph is active, the task is assigned
to the response's node and boot ID, the generation and attempt UUID match,
request/input digest matches, cancellation is clear, and coordinator time
is strictly before both task and graph deadlines. A valid terminal result
may prove admission when the original acknowledgment was lost.

Validate operation/backend version, encoded type, canonical byte encoding
and bounded byte length, then recompute SHA-256 and independently check
operation correctness for the M2 allowlist. Insert the content-addressed
artifact and committed task result atomically. Recheck the current fence
and time before commit if validation ran outside the transaction. A late,
unknown or superseded response may be logged but cannot change accepted
graph state. A duplicate of a committed result is a no-op; a conflicting
duplicate is a protocol error. Digest conflicts fail closed.

A downstream task becomes eligible only when all parents are committed
successfully. Verify stored bytes against the accepted artifact manifest
before enqueueing or dispatching, and bind the selected input digest to
the new request. The worker checks the input type, size and digest before
execution. A failed or cancelled dependency blocks descendants. M2 stays
within the 64 KiB wire cap including framing; large blobs, content stores
and nondeterministic backends are later work.

## Enrollment and resource boundaries

Use an administrator-owned allowlist binding node ID, endpoint, account,
independently provided host key and protocol versions. SSH must be
public-key only with strict host pins, a fixed command, source restrictions
and no forwarding, TTY, public bridge, or general shell/subsystem.
Use ephemeral CI keys and guest-only networking. Never publish private
keys or credentials. Changes in boot identity invalidate cached capability
and worker ownership; rediscover over the authenticated channel before
scheduling. Revocation stops new dispatch and fences outstanding results.

Probe on enrollment and every 2 seconds, with non-overlapping probes and
bounded I/O. Advertise operation/version, CPU backend, slot count, usable
memory and worker boot ID; clamp claims to configured caps. Initially
reserve one slot, at most one CPU and 128 MiB of execution memory per
worker, with enforced cgroup/launcher limits, bounded input sizes and a
separate memory allowance for the coordinator service. Record actual caps
and observed peak usage. Configure a bounded executor and queue so failed
peers cannot starve local CLI traffic. Verify Linux SSH restrictions on
disposable guests before the distributed test.

## Acceptance and measurements

Boot two separate QEMU guests on a single disposable GitHub-hosted Linux
runner, each with a distinct boot ID, kernel and journal, 2 vCPUs and
1.5 GiB RAM. Pin the M2 ISO, Git revision, original build run and digests.
Use the serial acceptance path and an isolated virtual link, no physical
disks. Prove negative enrollment, forced-command restrictions, and
capability discovery. Submit a graph whose normalize task runs on A,
first hash on B, and second hash on A. Compare every output with an
independent reference, including the cross-node dependency values.

- Lost acknowledgment: a test transport barrier drops just the first
  response after B has durably admitted or committed the request. Keep B
  reachable, query it with the same UUID, reconcile exactly one accepted
  result with zero new generations, and assert the worker ran the unit once.
  Repeat the coordinator-process-restart variant against the same database.
- Offline worker: let B complete at least one earlier unit, then block
  only B's relay after admitting a later one and before its result is
  delivered. Confirm offline detection and expiry. Retry the affected
  pure task on A with a higher generation, restore B, inject its recorded
  late reply, and assert rejection, correct final output, one publication
  per task and release of B's old reservation only after reconciliation.
  Use deterministic barriers, bounded partitions and explicit assertions,
  not sleeps as the fault trigger.

Proposed starting budgets: 240 seconds to boot each guest; 3 seconds per
RPC; 2-second heartbeat cadence; 10 seconds from last successful heartbeat
to offline marking; 15-second attempt lease; at most two attempts per
unit; 60-second graph deadline. The offline recovery gate is 45 seconds
from injected interruption to accepted final result. The healthy completion
target is p95 <= 20 seconds.

Run five warmups (discard from latency percentiles), 30 healthy trials,
30 same-run local-only baseline trials and 20 trials per fault scenario.
Use fresh graph IDs, clean terminal fixture state, and retain raw traces.
Use nearest-rank p95 (sorted rank ceil(0.95 * n)); report median, p95,
maximum and n. Do not drop failed or timed-out trials: fail the acceptance
gate and report them separately alongside successful-latency samples.
Do not pool TCG and KVM.

Measure healthy end-to-end from durable graph admission to accepted final
result; queue time from dependency-ready to durable assignment; RPC from
request send to validated reply or timeout; worker compute from actual
start to terminal result on that worker's monotonic clock. Measure
detection and recovery from coordinator-side markers. Do not subtract
timestamps across hosts. Publish runner/QEMU/firmware, guest boot and
service logs, authentic node and boot IDs, capability/cap records,
graph/task IDs, generations, UUIDs, dispositions, resource samples,
artifact digests, independent reference outputs and machine-readable
timings. Require zero stale/duplicate commits and zero leaked reservations
after reconciliation in every trial.

## Proposed change sequence and open decisions

1. Add scheduler lease/fence state and commit tests with a fake clock.
2. Extend worker protocol and idempotent admission, then test conflicting
   requests, tombstones, executor bounds and worker restart.
3. Wire opt-in registry, bounded transport and Linux SSH isolation, then
   test allowlist, negative auth, probe expiry and local CLI responsiveness.
4. Add deterministic two-node DAG and fault tests against simulated peers.
5. Package the M2 variant and run hosted two-guest acceptance for the exact
   published artifact; preserve full success/failure evidence.

Before coding, confirm the proposed 128 MiB worker cap against the actual
service footprint, choose whether protocol v2 needs a legacy translation
path (default: reject incompatible remote peers), and decide which
cgroup/launcher owns timed-out child cleanup. Also confirm whether to
include coordinator boot-ID-change fault injection in M2's hosted matrix
or cover it at the scheduler-test layer; it must be fenced either way.
Large artifacts, accelerated backends, multi-coordinator consensus,
and full-machine reboot persistence stay outside M2.
