# Dell Debian inference worker deployment

This deployment uses the existing `community_grid.server`, `worker`, `grid_relay`, and inference coordinator. It is separate from the native `worker_v2` text-operation protocol. The inference API binds to `127.0.0.1:8767`; the Lenovo reaches it only through SSH on the Dell's existing port 22. No inference HTTP port is exposed on Ethernet.

## Identity and assets

- Dell: `192.168.137.252` on the direct link; Lenovo source: `192.168.137.1`.
- Confirm the Dell host's Ed25519 key at its console before installing a pin. The verified Debian installation used `SHA256:VhV4rU+jOvXXaTiklEYkpmY58ZSB204z9dkTYr1Ixec`. Do not accept a changed key automatically.
- The approved Lenovo client public key has fingerprint `SHA256:4P6uSUrMa4e1kOQLwwS2p+gKEIPfIPZO8ToomS+DPzM`. Its private key and the Dell host pin stay on the Lenovo, outside Git.
- The existing llama.cpp b6364 Linux binary is pinned by SHA-256 `4dbed0b2d945dd9752f8945942209e0eede6d4ad46c067a81f7051d0c3796963`. The Qwen2.5 1.5B Q4_K_M GGUF is pinned by SHA-256 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`. Verify both after transfer. The Dell i5-2500 has AVX but lacks AVX2; the verified bundle selects its Sandy Bridge CPU backend.

## Deployment boundary

The new source and assets are staged in separate directories under `/home/cgos-admin`; the existing `/home/cgos-admin/community-grid-odysseus` snapshot is preserved. Review `distro/inference/install-dell.sh` before running it with sudo. Its three arguments are the new source directory, verified asset directory, and Lenovo public-key file. It refuses pre-existing `/opt/cgos`, `/etc/cgos/inference`, `/var/lib/cgos-inference`, or `cgos-infer` account. It checks both asset hashes, loader dependencies, public-key fingerprint, and the existing sshd configuration before changing the system. It installs only the missing Debian `git` package, creates a locked-password `cgos-infer` system account, copies the runtime and assets, generates a local operator token, installs the service and forced-command relay, then enables the service. It does not alter the admin account, sudo policy, main SSH configuration, firewall, partitions, or the existing snapshot.

The `cgos-infer` account has a single authorized key restricted to the Lenovo source address and a fixed relay command. `restrict` denies PTY and forwarding. Its password remains locked. The relay rejects any supplied SSH command and accepts only the worker's fixed GET/POST API paths. The operator token is generated on the Dell, kept in an account-owned mode-0600 file, and read by both the relay and loopback worker. It is not printed, committed, or placed in the unit file. The worker stores prompts and results in `/var/lib/cgos-inference/jobs.sqlite3` with an account-private directory; treat that file as sensitive.

`cgos-inference.service` runs unprivileged and restarts after failure. Logs go to `journalctl -u cgos-inference.service`. The unit caps the total service at 6 GiB and four CPUs; each model job is capped at 4 GiB address space, four threads, 90 seconds, and one running slot. It is enabled for later boots after verification.

## Predeployment smoke, 2026-11-16

The Lenovo ran one request through the existing coordinator, pinned SSH connection, and relay while the new code ran temporarily as `cgos-admin`. Job `a94c1ac0-fcac-4c6f-8416-d39024ca3077` completed on the Dell with the answer beginning "Two plus two is four." The Dell SQLite row independently recorded the same UUID, `inference` kind, and `completed` status; `integrity_check` returned `ok`. The single request generated 64 tokens in 8.82 seconds end to end. The Dell listener was `127.0.0.1:8767`, and it was closed after the smoke. The temporary token was removed. This verifies one distributed inference request using the existing backend. It does not yet validate the privileged service installation, restricted runtime account, restart handling, or boot persistence.

## Installed-service acceptance, 2026-11-16

The operator ran `install-dell.sh` with sudo after review. Debian installed `git`, `git-man`, and `liberror-perl`; no packages were upgraded. `cgos-inference.service` is active and enabled, has `Restart=on-failure`, and reports zero restarts. Its HTTP listener is `127.0.0.1:8767`. The unchanged Dell Ed25519 host-key fingerprint is `SHA256:VhV4rU+jOvXXaTiklEYkpmY58ZSB204z9dkTYr1Ixec`.

The Lenovo's pinned-key connection to `cgos-infer` obtained an authenticated capability response with the expected model digest and llama.cpp revision. A remote shell command was rejected, and a different Lenovo key failed authentication. An unauthenticated POST to the loopback job API returned HTTP 401. The native `cgos-admin` sudo policy still requires a password.

The Lenovo coordinator submitted exactly one installed-service job, `2af324d6-da51-4a44-a7d7-8ac4f026d864`. It completed in 9.15 seconds end to end and generated 64 tokens. The text began "2 + 2 is 4." A separate authenticated relay read returned the same UUID, `inference` kind, `completed` status, and token count. The service cgroup reported nonzero CPU use after the request. This validates one inference request on the Dell through the restricted transport. Restart recovery and survival across reboot have not been exercised; no reboot was authorized.

## Coordinator configuration

After installation, make a Lenovo-local JSON file, outside Git, with a single peer:

```json
{
  "dell": {
    "address": "192.168.137.252",
    "user": "cgos-infer",
    "identity_file": "C:/Users/joris/.ssh/community_grid_ed25519",
    "known_hosts": "C:/Users/joris/Documents/Codex/2026-11-16/codex-task-autonomous-debian-installation-for/outputs/Dell-JRG-Known-Hosts",
    "forced_command": true,
    "bind_address": "192.168.137.1"
  }
}
```

`community_grid.coordinator.ssh_transport` keeps strict host-key checking, disables agent/forwarding reuse, and sends no remote command in this mode. The Dell's forced command supplies the relay and token file. A successful SSH connection and authenticated capability response are both required before enrollment. One explicit `--submit` request is then enough for a smoke test. Confirm the returned job UUID, completed status, nonempty generated text and token count, and the same UUID in the Dell's SQLite queue. Keep the one-request smoke separate from benchmarks.

## Remaining Odysseus work

The coordinator's inference API is a Python command, not an OpenAI-compatible network service. Odysseus integration still needs a localhost API adapter with `/v1/models` and `/v1/chat/completions`, a request-to-graph or coordinator mapping, streaming/cancellation behavior, model identity and error contracts, authentication, and end-to-end tests against the Dell. None of these are implied by a successful worker smoke test.
