# M2 Task 3 worker enrollment

Local mode is still the default. Run a separate coordinator process with
the existing Unix socket and a reviewed administrator manifest:

    PYTHONPATH=runtime python3 -m community_grid.native --socket /path/to/api.sock serve --state /private/state --enrollment /admin/nodes.json

The M1 systemd unit is intentionally limited to Unix sockets. Its live
image and local-mode invocation do not enable outbound SSH. Packaged
distributed service configuration and multi-VM acceptance are future work.

The manifest has top-level integer version 1 and a workers array of at
most eight entries. Each entry has:

- node, address (literal IPv4), port, and account
- host_key (the two fields of an independently obtained Ed25519 public
  key) and fingerprint (its separately verified OpenSSH SHA256 fingerprint)
- identity_file (absolute path to the coordinator SSH identity)
- protocols (currently [2]), operations (allowlisted operation to version
  mapping, such as {"text.normalize":1}), cpu (1), memory_mib (16–128)
- revoked (boolean)

Keep the manifest and its parent directory under administrator control.
On Linux the file must be regular and must not be writable by group or
others. Keep the identity and state directory private. Obtain the host
key and fingerprint through a trusted channel before enrollment. The
loader calculates the fingerprint locally and writes a per-worker
known-hosts file. Neither key scanning nor trust on first use is involved.

Use the Unix socket's nodes action to see boot ID, online status and
clamped capabilities. Replace the manifest atomically and invoke
reload-nodes on the same socket to apply edits or revocations. A bad
reload leaves the last valid manifest in effect and returns an error.
Successful removal or policy replacement fences affected attempts in the
coordinator database before any new transport response can publish.
Remove the authorization from the remote account separately to revoke
its credentials there. Revoked worker leases remain quarantined until a
trusted enrollment allows reconciliation. Unknown, revoked and out-of-policy
placements are rejected on submission.

Probes and lease calls run in a bounded executor with at most one live
exchange per peer. Probes run every two seconds when a peer is idle. A
peer becomes ineligible ten seconds after its last valid probe. A valid
new boot identity replaces the cached capabilities and clears old replies.
Capabilities always intersect operator policy, with one CPU slot and the
configured worker memory limit. The existing GraphRuntime remains the
only SQLite graph writer. Background exchanges never publish results
directly. Distributed state uses an additive enrollment table in
distributed-graphs.db, separate from the M1 local database.

For a worker, configure OpenSSH to listen only on the approved interface
and source-restrict the key and account. Use a dedicated unprivileged
user and an administrator-owned forced relay script. Set public-key only
authentication, StrictModes, DisableForwarding, PermitTTY no, PermitUserRC
no, PermitUserEnvironment no, and no Subsystem entries. The script invokes
community_grid.physical with a fixed state database, coordinator and node;
it must never evaluate requested commands. Run the Task 2 executor under
the same service identity. Its Linux address-space limit applies to the
child; coordinator and worker-service memory are separate. Keep the
runtime code, trust material and sshd configuration immutable to the
relay account. Do not enable an agent socket in the worker environment.

The disposable Linux test at runtime/tests/integration/enrollment-openssh.sh
creates ephemeral keys and a loopback-only sshd. Run it only on a disposable
Ubuntu runner with sudo and OpenSSH. It exercises the actual relay and
scheduler, checks rejected connection and session types, and retains public
diagnostics and result hashes. Keep private keys outside evidence and
source control. This is a same-runner integration test with isolated
processes, not two-guest acceptance.
