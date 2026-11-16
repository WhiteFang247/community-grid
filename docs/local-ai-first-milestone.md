# Community Grid Local AI — first implementation scope

## Product decision
Build a native Debian Linux application, not a replacement OS. Use Odysseus as the local agent UI/runtime, connected through its supported API integration to Community Grid's local model gateway. Verify the actual Odysseus integration API before implementation; do not assume an endpoint format.

## Physical layout
- Lenovo: Debian on removable USB, XFCE and Codex, coordinator, primary model backend, dashboard.
- Dell: existing or user-reinstalled Debian, lightweight worker, optional independent tasks or model backend.
- LAN only; no internet-facing worker ports.

## Minimum vertical slice
1. Inventory and reuse existing coordinator, worker, relay, model gateway and authentication.
2. Installable Linux services and launcher, with local GUI for pairing and device eligibility.
3. Persistent authenticated SSH connection prototype behind feature flag, preserving pinned host keys and restricted commands; fallback to existing transport.
4. Connect Odysseus to local model API, test one end-to-end prompt and one authorized tool task.
5. Dispatch supported independent work to Dell, return result to agent.
6. Pause Dell eligibility, verify no new work assigned, resume.
7. Keep user-set CPU/RAM caps enforced on each node.

## Scope and validation
Prioritize application-level smoke checks over new benchmark campaigns or full M2 matrix. Never assume prior tests prove a changed runtime works. Verify startup, authentication, API response, pause/resume, and no unauthorized task execution. Keep existing evidence and M1 artifacts unchanged.

## Hardware safeguards
No automatic USB imaging, disk partitioning, OS reinstall, Dell GPT repair, reboot or destructive operation. Require explicit device verification and user approval before writing removable media. Do not embed tokens in repository or build artifacts.

## Acceptance
A local Odysseus prompt reaches a local model via Community Grid, a supported task runs on Dell with a visible result, pause blocks new assignments, resume restores eligibility, and Lenovo remains usable. Record any performance or capability limitations without claiming model-level speedup.
