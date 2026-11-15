# CGOS M2 Task 2 worker execution

The Phase 1B relay accepts v2 only when configured with both
`--coordinator ID` and `--node ID`. The IDs come from trusted local
configuration. The Linux worker process reads its own kernel boot ID.
The legacy unversioned route continues to serve M1. The v2 route rejects
missing and incompatible versions. This change adds no enrollment or
network listener.

Run the executor separately with the same database path, node,
coordinator, and memory settings as the relay:

    PYTHONPATH=runtime python3 -m community_grid.worker_v2 STATE.db --coordinator A --node B

Requests contain integer `version: 2`, `action`, and, for all actions
except probe, the exact Task 1 lease. Submit also contains a text value.
The canonical request hash covers the lease fields excluding
`request_digest`. The separate input digest covers canonical JSON for
the value. Coordinator monotonic deadline is opaque to the worker.
The worker uses the fixed relative execution budget starting at admission
and never extends it on replay. Probe returns its node and boot identity,
CPU slot, supported operations, and configured RAM bound.

SQLite commits the complete admission before the relay replies. Attempts
are keyed by configured coordinator and UUID. Exact replay returns the
current durable state, and a conflicting lease or value is rejected.
Admission does not launch work. Status returns queued, running, or the
recorded terminal result. Cancellation of an unknown UUID commits a
tombstone. A delayed identical submission returns cancelled without
execution. Cancellation of a running attempt records intent, and the
executor reaps the child before publishing terminal state. Cancellation
after completion returns the existing result. The coordinator still
decides which result can be published under its lease.

The executor has one owner per database, enforced by Linux flock. The
child inherits the lock, exits on parent death, and checks its expected
parent to close the launch race. A replacement owner never launches a
stranded running UUID again. It marks it failed (or cancelled if that
intent was committed). Queued work from the current worker boot may still
run within its original budget. Timeout, cancellation, and graceful
executor stop kill the child process group when needed and reap the child.
Only the built-in, non-spawning text operations are available.

Each child is pinned to one available CPU and enforces a hard Linux
RLIMIT_AS address-space ceiling at the reserved MiB, within the configured
worker maximum of 128 MiB. The interpreter starts before setting the
child limit. The service has a separate memory footprint. The reported
service RSS is process lifetime high-water RSS. Child RSS uses its reported
high-water value when available and procfs samples for interrupted work.
An interrupted child's sampled peak can undercount short-lived spikes.
There is no aggregate cgroup memory claim or support for operations that
spawn independent descendants. The admission queue is bounded to 32
active attempts and wire messages to 64 KiB.

Migration is additive. V2 adds worker_v2_attempts and an adjacent
executor lock file. Existing attempts are left intact and are never
interpreted as v2. Keep the state directory private and do not remove the
lock file while any worker can be running. Terminal states and tombstones
are retained for the test run, with no automatic pruning. Reconcile all
outstanding attempts before retiring the run database. Use a fresh
database per coordinator and worker deployment. The worker has no network
trust of its own in this phase, so do not expose this interface to
untrusted callers.

Linux verification commands:

    PYTHONPATH=runtime python3 -B -m unittest discover -s runtime/tests -v
    PYTHONPATH=runtime python3 -B runtime/benchmarks/worker_memory.py
