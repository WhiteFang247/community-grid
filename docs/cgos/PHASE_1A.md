# CGOS Phase 1A

The isolated cgos/phase-1a worktree starts at f09ddd4 and includes a copy of the feature worktree's Python runtime and tests, including outstanding Windows work. The feature worktree and experimental evidence were preserved. Graph execution is a local Python API and simulator. Existing inference and SSH remain separate, with no graph adapter for real workers yet.

## Schema and application interface

A graph is a JSON object with integer version 1, nonempty id and owner strings, and a units map keyed by unit identity (1–32 entries, 64 KiB total). Each unit has op, input_type, output_type, depends (unit IDs), nodes (eligible node IDs), cpu (thread requirement), ram_mib, timeout (attempt seconds), attempts (1–3) and side_effects (none). Supply a typed literal value or a from reference to a listed dependency with matching output type. Input references currently select one parent. Additional dependency edges can order work. Deadlines are per attempt and start at reservation. Waiting for an eligible worker is open-ended until cancellation.

GraphRuntime exposes submit(graph, owner), status(graph_id, owner), cancel(graph_id, owner) and result(graph_id, owner, unit). A trusted application adapter provides owner from its authenticated context. This argument is an ownership check within the library. It is not a user authentication protocol. Identical graph submission is idempotent. A conflicting definition for the same global ID is rejected. The coordinator loop calls tick; the simulator advances work explicitly.

## Operations and placement

Registry v1 maps operation names to typed pure functions. text.normalize collapses whitespace (text → text), text.sha256 hashes UTF-8 bytes (text → hex text) and data.aggregate wraps an input array as an object with an items field. The simulator has no shell execution or arbitrary-code input. The registry is application-agnostic. No inference-specific logic is used for placement.

DAG validation rejects cycles, missing dependencies, unknown operations and incompatible edges. The scheduler walks ready units and candidate node IDs in sorted order and picks the first online capable node with sufficient reported resources and a free slot. The slot reservation applies across graphs in a database. Used resources are compatibility checks in simulation. Physical enforcement requires a real trusted adapter.

## Persistence and recovery

SQLite stores graph definitions, unit/dependency state, attempt history and assignments as transactional graph documents, with a separate artifact table indexed by SHA-256. A tick commits the attempt UUID, node, input and deadline before a subsequent tick contacts the node. On coordinator restart, use the same database and adapter identities. Poll before resending. A lost response retains the reservation and the same ID. An acknowledged attempt later confirmed missing is treated as failed. A confirmed failure may receive a fresh ID within the pure operation retry budget. Unreachable nodes keep their reservations until reconciliation. A cancelled or expired attempt cannot publish even while its node remains unreachable.

Unit states are waiting, assigned (includes dispatched or running), completed, failed, cancelled, timed-out and blocked. Failed or cancelled prerequisites block downstream execution. Cancellation requests for assigned work remain pending while the node is silent. Accepted results are canonical JSON artifacts with digest, size, type and producer attempt. Publication verifies claimed digest and output type and atomically stores the result manifest with its terminal state. Stale, duplicate and expired attempts cannot replace the result. Result reads recheck the digest. Exactly-once publication is a SQLite property here. Execution can repeat after recovery if a pure operation is retried.

The single coordinator is the trusted publisher and has exclusive operational ownership of its simulated nodes. The reference implementation does not include persistent simulated node storage, graph-level deadlines, external transport, or hostile worker isolation. Integrity checks detect mismatched bytes and cannot prove a worker computed the right answer.

## Security and Phase 1B proposal

The existing SSH supervisor disables forwarding and TTY and uses pinned host trust, but still has an account shell. Before exposing a graph relay, review a separate relay-only key/account with a fixed server-side forced command, restricted key options, a minimal JSON allowlist and a private loopback token. Stage assets separately under an operator-owned process. Prototype on a separate live test environment. Preserve the existing identity and configuration, confirm the new entry is isolated and test its removal as rollback. An operator can remove the experimental key/account configuration and close the test process while retaining the existing supervision path. Phase 1A made no SSH or active service changes.

After review, Phase 1B should use a bounded two-unit graph on Lenovo and Dell. Normalize a synthetic fixture on Lenovo, then hash the bounded normalized artifact on Dell through a reviewed typed adapter. First prove restart reconciliation and digest agreement against a single-host run. Scope a failure to the experiment-owned worker after assignment, and require one accepted output, no stale publication, no orphan process, and verified cleanup. Seek separate physical-execution approval once the adapter and relay boundary are reviewed. This proposal leaves the active Odysseus deployment outside the experiment.

## Validation

The Windows RAM test mocked a Linux-only memory function. The fixture now mocks the active platform's memory probe. The memory cap test read partial communicate output from a Windows timeout before its reader completed. It now checks the full captured output after Job Object cleanup. Passing these tests does not qualify native inference on the Lenovo.
