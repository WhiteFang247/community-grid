# Phase 1B physical runtime

## Readiness

The coordinator persists assignments before dispatch. It queries the same attempt UUID after lost replies and rebuilds reservations from SQLite on restart. Placement is lexical among eligible nodes, with one reserved slot per node across graphs. Cancellation and expiry fence publication immediately and keep an unreachable slot reserved pending reconciliation. The worker commits a replayable terminal response or cancellation tombstone in SQLite. The coordinator independently recomputes each pure operation and checks its canonical JSON artifact SHA-256 before accepting a result.

The two allowlisted operations accept and emit text. The relay exchanges one JSON request and response per SSH process with 64 KiB ceilings. Neither the scheduler nor the wire request constructs remote shell commands. The new endpoint uses OpenSSH key authentication, a dedicated host pin, no remote command, no forwarding, and no TTY. Enrollment is the operator's explicit choice of endpoint, key, account, and pin. There is no discovery or automatic enrollment.

The current implementation has one coordinator owner, serialized SQLite execution, and polling with bounded SSH exchanges. Deadlines fence results but an in-flight SSH round trip can delay observation of expiry. Worker UTC timestamps are diagnostic. Check clock alignment before comparing hosts. Worker RAM is a placement declaration, not an OS memory quota. The operations are single-threaded and size-bounded. Cancellation is serialized with the short computation and cannot interrupt it mid-operation. Exactly-once refers to accepted publication, and an uncommitted operation may run again. State and tombstones accumulate for the duration of the test. Loss of a worker database after acknowledgment cannot recover its old result. A worker with a trusted identity can still refuse requests or falsify timing.

## Approved execution only

1. On the Lenovo use Python 3 and OpenSSH client from the Phase 1B runtime checkout. On the Dell console independently confirm live USB boot, internal SATA partitions unmounted, private writable tmpfs, free port 22227, Python 3, OpenSSH server, clock alignment, and the actual direct-link IPv4 on both hosts. Confirm that an unprivileged account dedicated to the experiment and authorization to launch a separate foreground sshd are available. Stop if console approval, credentials, account provisioning, or a scoped network path are missing.
2. Generate a fresh passphrase-protected Ed25519 client key on the Lenovo and load it into an available ssh-agent for the test. Keep its private key and a dedicated known-hosts file outside Git. Transfer only its public key and reviewed runtime files to Dell live RAM through an approved console-controlled path. Stage the community_grid package beneath /dev/shm/cgos-phase1b. Keep code, host keys, daemon config, and authorized_keys administrator-owned and immutable to cgos-exp. Create a separate cgos-exp-owned mode 0700 state subdirectory. No models or inference binaries are required.
3. Add the client public key to the dedicated authorized_keys with options restrict,from="LENOVO_DIRECT_IP". Install the following fixed relay.py in the administrator-owned directory.

       import sys
       sys.path.insert(0, "/dev/shm/cgos-phase1b")
       from community_grid.physical import main
       sys.argv = ["relay.py", "/dev/shm/cgos-phase1b/state/attempts.db"]
       raise SystemExit(main())

4. Generate a dedicated host key on the Dell console.

       ssh-keygen -q -t ed25519 -N '' -f /dev/shm/cgos-phase1b/host_ed25519

   Replace the template's loopback ListenAddress with the independently confirmed Dell direct-link address only. Leave port 22227 and all restrictions in place. Confirm with sshd -t and sshd -T against the staged file and inspect effective paths, user, binding, and forced command. Confirm the host key fingerprint with ssh-keygen -lf on the console. Transfer the public host key out of band and install it as a single [DELL_DIRECT_IP]:22227 entry in the dedicated known-hosts file. Recheck the fingerprint before connecting. The only new listener is the experimental SSH port, with key-only authentication and the client source restriction. Neither dashboard nor relay needs an HTTP listener. Do not alter existing services or firewall policy.
5. From the Dell console with separately approved privilege, start the isolated listener in the foreground using the commands below. The bounded process is separate from any existing SSH service.

       /usr/sbin/sshd -t -f /dev/shm/cgos-phase1b/sshd_config
       timeout -k 5s 180s /usr/sbin/sshd -D -e -f /dev/shm/cgos-phase1b/sshd_config

6. Before the graph, verify that the experimental key cannot request a shell command, PTY, forwarding, or SFTP and an unenrolled key cannot authenticate. For forwarding, require failure of an explicit remote forwarding request with ExitOnForwardFailure=yes. Verify only the expected listener exists and stop if any check fails. From the Lenovo runtime directory use a fresh absolute state path whose parent exists.

       python -m community_grid.physical_demo STATE DELL_DIRECT_IP cgos-exp PRIVATE_KEY KNOWN_HOSTS

7. The Lenovo normalizes the input to “seed library” and the Dell hashes its UTF-8 bytes. Capture stdout JSON and both SQLite databases. Compare phase timestamps, acknowledgment and terminal outcomes by UUID. Measure end-to-end using elapsed_seconds and execution duration using finished_at minus started_at on the same host. For dispatch latency, time the SSH exchange on Lenovo with a monotonic clock. Keep transport time separate from execution time and save any timeouts or retries with the evidence.
8. In a fresh run, inject a lost acknowledgment by wrapping the transport and raising ConnectionError once after a successful submit response. For coordinator restart use the same state directory and --resume. For a disconnect test stop only the separate foreground sshd after the Dell assignment and restart it with the same key and tmpfs database, within the run window. If the deadline passes, confirm zero accepted late publications and retained reservation until reconciliation. Keep configuration and state isolated across runs.
9. Stop the isolated daemon at its console. Check port 22227 is closed and that no experimental sshd or relay processes remain. Capture sanitized evidence, unload only the experimental key from the Lenovo agent, and remove its private key and dedicated host pin if the session is retired. Delete the explicitly staged live-RAM files and undo any separately approved temporary account configuration. Verify existing dashboard, SSH, and mount state. Do not reboot or touch internal disks.

Success requires matching reference output and artifact digest, expected placements, one accepted publication per unit, rejection of non-relay access, and clean teardown. Stop on host-key mismatch, unexpected listener or mount state, authentication bypass, or cleanup failure. This preparation validates the relay subprocess and OpenSSH configuration parsing. The Linux daemon and authentication behavior still require validation in an isolated Linux environment or as separately approved console preflight before cross-device dispatch.
