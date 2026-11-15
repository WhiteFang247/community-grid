# M2 Task 1 scheduler leases

The Phase 1A scheduler ran a single SQLite graph writer and checked attempt
UUIDs before publication. It kept one active reservation per node. An expired,
unreachable attempt could retain its reservation and stop the task instead of
permitting a replacement. Phase 1A also used wall-clock deadlines that could
not be compared across coordinator boots. The existing graph tests passed
before changes.

Opt in to lease mode by passing a trusted coordinator boot ID to GraphRuntime.
Use a dedicated database and one running scheduler. The Task 1 peer contract
is an in-process adapter with an authenticated boot identity, eligibility
check, lease_dispatch, lease_status and lease_cancel. Task 2 supplies the real
worker-side protocol and verified identity. The existing local constructor
and legacy worker adapter keep their existing semantics.

The assignment and reservation are committed before dispatch. A persisted
lease records graph and task IDs, node and boot IDs, generation, attempt UUID,
fixed deadline, relative budget, CPU and RAM, and canonical input and request
digests. Status must echo the exact canonical lease. Publishing additionally
requires the scheduler's assigned worker identity, current boot/fence, valid
deadlines and independently recomputed output. SQLite atomically records the
artifact and terminal task. Duplicate accepted replies do not republish.

Timeout or confirmed loss can retire a fence and schedule a new generation
if the graph still has time and attempts. Quarantine is independent of the
logical task so fallback does not release the original node's execution slot.
An unreachable node or one with unknown boot identity remains quarantined.
Release requires a matching terminal response after cancellation or a trusted
new worker boot identity. Lost acknowledgments reconcile by the same UUID.
One CPU slot and the configured worker memory budget (default 128 MiB) govern
placement. This accounting does not enforce operating system memory or CPU
limits.

SQLite migration is additive. Legacy graphs and artifacts retain their tables
and JSON shape. Lease mode creates scheduler_epoch and lease_quarantine and
adds the coordinator boot ID to new graph records, plus lease metadata to
assigned records. Graphs created in one mode are inaccessible in the other.
Same-boot process restart preserves leases. On a coordinator boot-ID change,
monotonic deadlines cannot be recovered; atomically time out old unfinished
graphs and quarantine their assignments while keeping committed artifacts
readable. The previous runtime is fenced by the stored epoch. Older M1 code
must use a separate database and must not open an M2 lease database. Back up
the database before migrating or downgrading. No filesystem migration is
required for the accepted M1 image.

Task 2 must define the durable worker admission response, enforce resource
limits, bind peer identity to authenticated channels, and align worker
idempotency and cancellation with the scheduler's fixed lease. It must
also provide a scheduler owner lock if multiple processes can initialize
one database. Task 1 assumes a single coordinator process and serial tick
calls. Cancellation and publication are transactional; preventing an already
in-flight RPC from reaching the worker requires the worker tombstone.

## Reference memory measurements

Run `PYTHONPATH=runtime python3 -B runtime/benchmarks/reference_memory.py`
from the repository root on Linux. The benchmark runs ten fresh baseline
processes and ten fresh workload processes. Each workload verifies 200 pairs
of normalization and SHA-256 operations on 32,000-character input. Linux
ru_maxrss measures process high-water RSS in KiB. Wall time includes process
startup, imports, payload preparation and execution.

The [hosted benchmark](https://github.com/WhiteFang247/community-grid/actions/runs/43754217424)
measured Python 3.12.3 on Ubuntu 24.04 x86-64. Workload median and maximum
RSS were both 20,320 KiB (19.84 MiB). The baseline median was also 20,320 KiB
and its maximum was 20,380 KiB. Workload median time was 30.6 ms for 200
pairs, and median process wall time was 73.1 ms versus 42.8 ms for baseline.
The measurement resolves no workload RSS increase above baseline. The
provisional 128 MiB cap appears sufficient for these operations in an
isolated process. SSH, worker admission, database, cgroup and service
overhead are not measured here. Task 2 must measure the complete executor
and verify enforced limits.
