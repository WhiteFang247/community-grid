# M2 Task 4 distributed graph integration

The scheduler, versioned durable worker and SSH enrollment path existed at
the start of Task 4. The real SSH fixture exercised only one worker, and the
native CLI could not cancel a distributed graph. The coordinator also relied
on an operator to keep multiple instances out of the same state directory.
A damaged dependency artifact could raise out of a distributed scheduler tick.

Distributed mode now takes an exclusive Linux process lock for the entire
coordinator lifetime, including drain of remote calls. The Unix API can cancel
a graph in distributed mode and reports the assigned node in graph status.
An invalid dependency artifact fails the dependent task and blocks its
descendants. The scheduler remains the only authority for graph state and
publishes only through the existing transactional result path. M1 still uses
local mode by default and its installed service and ISO are unchanged.

The fixture at distro/m2/reference-graph.json normalizes on A, hashes that
verified value on B, and hashes B's verified result on A. The reference inputs
and each intermediate output and artifact hash are checked independently.

The deterministic tests use the real graph scheduler, async peer state machine,
and v2 SQLite admission, with a controlled executor and fake clock. They record
the lost-acknowledgment barrier after a durable commit. One test exits the first
Python coordinator process and opens the same database from a second process.
Both require one B execution, one accepted publication and no new generation.

The offline barrier is triggered after B completed the first hash but after it
has durably admitted the final task. A test reservation forces B to receive that
assignment first. The test advances the coordinator clock to detect offline
status and lease expiry, then allows A to complete a new generation. A late
result from B is rejected, and B's old slot is quarantined until trusted
reconciliation confirms its terminal state. The cancellation test withholds
submission until the worker has a durable tombstone, then releases the delayed
request and asserts no execution and no publication.

The Linux integration also runs two actual worker executors over separate
loopback-only OpenSSH listeners, each with its own host key and state directory.
They share the disposable runner's kernel and boot ID. This proves protocol
integration and authenticated placement without claiming two independently
booted guests. No physical hardware is used. The existing Unix socket test
covers local status responsiveness while a transport call is delayed.

Run the unit and simulation suite with
`PYTHONPATH=runtime python3 -B -m unittest discover -s runtime/tests -v`.
Run the OpenSSH integration script only on a disposable Linux runner with
OpenSSH and sudo. The branch workflow saves source identity, logs, placement,
result digests, and hashes of the evidence files.

Next, package opt-in coordinator and worker roles in an M2 image, provision
separate worker boot identities and guest-only networking, and run the
two-guest acceptance and timing matrix. Task 4 does not validate VM boot,
cross-kernel clock behavior or hardened production deployment.
