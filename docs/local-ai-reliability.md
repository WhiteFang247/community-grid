# Local AI startup and recovery (Lenovo + Dell)

The Dell `cgos-inference.service` is enabled for `multi-user.target`. It runs as
`cgos-infer`, listens on Dell loopback only, restarts on failure after five
seconds, and retains `MemoryMax=6G`, `CPUQuota=400%`, and one-job concurrency.
The service needs only loopback at startup, so it does not depend on an
external network-online target. The Lenovo makes contact through the pinned,
forced-command SSH profile when the network is available.

On the Lenovo, the limited-user **Community Grid Local AI** Task Scheduler
task starts at `joris` sign-in. It stores no password and runs
`outputs/Community-Grid-API-Supervisor.py`, a shim into the versioned
`scripts/windows/local_ai_control.py`. The supervisor starts Odysseus from its
existing checkout and Community Grid from this checkout. Both bind to
`127.0.0.1` (`9700` and `8787`). The previous Startup-folder launcher was
removed after the task was verified, so there is one sign-in launcher. Startup
before sign-in is not configured: Odysseus requires a user-profile credential
file and an interactive user environment. No Windows security policy changed.

From Lenovo PowerShell, with the original task workspace as the current
directory:

```powershell
& .\outputs\Community-Grid.cmd status
& .\outputs\Community-Grid.cmd stop
Start-ScheduledTask -TaskName 'Community Grid Local AI'
```

`status` reports `HEALTHY`, `DEGRADED`, or `OFFLINE`. It checks the API's
`GET /health`, the authenticated Dell heartbeat, coordinator state, worker
model hash and backend revision, recent failures, Odysseus availability, and
the enabled Community Grid provider row. The health request does not load the
model. Use `Get-ScheduledTask -TaskName 'Community Grid Local AI'` to inspect
task state. `Community-Grid.cmd start` and `restart` are also available for
manual user-level operation, but use `Start-ScheduledTask` after stopping a
task-managed instance so the task remains the owner.

Logs are in the task workspace's `work` directory:
`community-grid-api-supervisor.log`, `community-grid-api.log`, and
`odysseus-supervisor.log`. The Dell service log is available through
`journalctl -u cgos-inference.service`. Logs should not contain credentials.
The supervisor avoids replacing an unknown listener and uses a single-instance
lock. It retries process exits with exponential delay up to 60 seconds and
cools down for five minutes after five short failures in ten minutes. The
Dell's systemd restart policy handles worker process failures separately.

The API accepts new work only when the pinned SSH relay reports the expected
model and backend and the worker is free. An incomplete coordinator job keeps
its UUID after a timeout or transport loss. Background reconciliation retries
that UUID; a new request waits for its outcome. Odysseus's `session_id` and
prompt identify a retry. Other clients should reuse an `Idempotency-Key` and
can inspect `GET /v1/jobs/{uuid}` after a timeout. A retry without either key
can be a new job after the old result resolves. There is no cancellation of
an uncertain Dell job.

Errors use the OpenAI `error` object: unavailable Dell, SSH host-key or
public-key failure (503), worker busy (429), unresolved or timed-out job
(503/504 with job ID), prompt over 4,096 UTF-8 bytes (400), model context
overflow (400), and worker internal failure (502). The model, 2,048-token
context, 64-token output cap, and one-job limit remain unchanged. This is one
remote worker inference path, not distributed model execution.

After a disconnection, check `status`; leave networking and SSH settings
unchanged and allow the bounded reconciler to reconnect. If the API is
`OFFLINE`, inspect supervisor/API logs and the scheduled task. If SSH host-key
verification fails, compare the Dell's ED25519 fingerprint at its local
console before updating known_hosts. Never disable host-key checking. If the
worker stays offline, inspect `systemctl status cgos-inference.service` on the
Dell through the pinned SSH profile. A service restart requires sudo and should
be done only after checking that no job is running.

Validation on 2026-11-16: the task was registered and manually started under
the limited user; both services and the provider reported ready. The Dell
service was restarted after a two-file context-error update, remained enabled
and active with its 6 GiB limit, and served the authenticated heartbeat. A
guarded stop of the API child at 15:45:26 was followed by automatic restart at
15:45:29 and `HEALTHY` status. The transport tests simulated a disconnected
Dell and verified bounded reconciliation and one dispatch for an uncertain
job. Odysseus then completed a short chat through the preserved provider;
coordinator job `1789671b-f597-495f-b8e3-dbe7b20d9b53` matched a separate
authenticated Dell record with the reply "Two plus two is four." and seven
output tokens. The runtime suite ran 91 tests with 10 skips. A full Windows
sign-out/sign-in, Windows reboot, and Dell reboot were not performed.
