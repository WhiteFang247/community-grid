# Local AI Odysseus branch audit — 2026-11-16

## Confirmed from GitHub
- Branch `feature/local-ai-odysseus` exists, 60 commits ahead of `main` at review time.
- `docs/local-ai-first-milestone.md` describes the intended Debian-native, LAN-only Odysseus application.
- `runtime/README.md` describes a foundation prototype; `README.md` explicitly says the checked-in runtime does not yet run an LLM, chat agent, or distributed workloads.
- Earlier Lenovo/Dell experiments are not yet incorporated into the checked-in runtime according to the README. Do not infer their code is present from the research notes.
- `docs/dual-mode-runtime-architecture.md` marks the distributed implementation pending.
- The existing local dashboard is read-only; it is not a working device eligibility control.

## First implementation slice (no new benchmark campaign)
1. Inventory the actual source tree and separately locate any previous coordinator, gateway, worker relay, and model runner code from the physical experiments. If not available, mark them as missing rather than inventing an integration.
2. Create a small versioned local model-gateway API adapter around a supported llama.cpp or Ollama server, loopback-bound by default. Verify Odysseus's real configurable provider interface before selecting endpoint format.
3. Implement persistent node enrollment and a per-worker eligibility state: available, paused. The worker enforces CPU/RAM caps; the coordinator must not dispatch new jobs to paused nodes. Existing jobs may finish unless explicit cancellation is supported.
4. Keep SSH authentication and host-key pinning; optimize connection reuse without broadening the network exposure. No internet-facing ports.
5. Package the coordinator/worker for Debian using systemd user or system services as appropriate, plus a minimal GUI to select nodes.
6. Wire an Odysseus prompt to the local model gateway, then route one explicitly supported independent tool task to Dell and display the result. Do not imply this accelerates single-model inference.
7. Run only essential functional smoke checks: local model response, authenticated Dell task, pause/resume no-new-dispatch behavior, and recovery when Dell is unavailable.

## Environment and safety
- Lenovo: Debian booted from removable USB; preserve its Windows disk and existing CGOS data.
- Dell: Debian worker; OS reinstall/PXE setup is a separate user-approved operation.
- Never select or format disks automatically, embed secrets in commits, or enable arbitrary remote shell commands.
- Preserve prior M1/M2 evidence; no new performance benchmark requirement for this milestone.

## Definition of done
Odysseus works through a local model endpoint on Lenovo; Dell completes one allowed independent task; pausing Dell prevents new assignments; resuming permits them; local operation continues when Dell disconnects. Document unsupported Odysseus API details and any limitations accurately.
