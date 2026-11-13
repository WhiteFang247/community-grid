# Community Grid OS: Built-in AI System Assistant

Status: architecture proposal, not implemented. Target branch: feature/community-grid-os.

## Product goal
A multilingual, local-first conversational interface that assists with first-boot setup, automatically recommends and installs compatible local LLM tooling with informed approval, and manages the host and Community Grid through auditable, permission-controlled operations. It must remain usable without a working model or internet connection.

## Components
1. **Bootstrap wizard (deterministic)**: hardware probe, disk/free-memory checks, connectivity checks, runtime installation planning, rollback/recovery. The installer must not depend on the LLM it is installing.
2. **Capability profiler**: CPU architecture/features, total and available RAM, storage, supported GPUs/backends, driver verification, thermals and benchmark measurements. Distinguish advertised Vulkan devices from verified LLM acceleration.
3. **Model catalog and selector**: signed/pinned model metadata including license, source, checksums, quantization, disk/RAM/context estimates, language support and tool-use evaluations. Benchmark eligible candidates locally; rank for latency, sustained tokens/s, memory headroom, quality and task type. Ask before downloading.
4. **Inference service**: initially llama.cpp with CPU fallback, optionally verified Vulkan/CUDA/other backends. OpenAI-compatible localhost API; future distributed inference routing through Community Grid.
5. **Assistant orchestrator**: multilingual chat, tool schema, planning, progress and verification. Prefer typed operations (inspect, package install, service control, file patch) to unrestricted command generation.
6. **Permission broker**: non-root assistant by default; separate privileged daemon with narrow typed operations, argument validation, per-action authorization, least privilege, timeouts, resource limits and audit trail. Shell execution is opt-in and restricted; never silently run untrusted instructions or exfiltrate secrets.
7. **Desktop interface**: local UI, action previews, dry-run, explicit approval for installations, privileged changes, destructive commands and remote node actions; show output and rollback guidance. No automatic remote command execution.
8. **Grid adapter**: authenticate/enroll nodes, advertise capabilities, schedule only supported workloads. Remote nodes enforce their own authorization policy.

## First-boot flow
- Boot into standard Linux desktop or recovery shell; run offline-capable deterministic setup.
- Probe CPU/GPU/memory/storage and check for an existing inference runtime.
- Present eligible model choices and expected quality/speed tradeoffs; recommend rather than silently choose.
- Ask for approval of package installs, downloads and privilege escalation.
- Verify hashes/licenses, install pinned dependencies, run smoke test, measure tokens/s.
- Launch local assistant with constrained read-only tools, then request permissions as needed.
- Optionally enroll into Community Grid with authenticated pairing.

## Security invariants
- Model output is untrusted; never execute it directly as root.
- Explicit approvals for system mutations and remote operations.
- Prompt-injection-resistant separation between data (logs/files/web) and user authorization.
- Local-only API by default, authenticated remote access if enabled.
- Audit logs must redact secrets; package sources and model artifacts verified.
- Recovery UI works when the model crashes or becomes unavailable.

## Milestones
A. Add hardware probe enhancements and machine-readable capability schema.
B. Implement deterministic model eligibility/recommendation and unit tests using synthetic hardware profiles; no downloads.
C. Add llama.cpp installation planner, version pins and dry-run mode.
D. Add isolated local inference service, model benchmarks and health checks.
E. Add chat UI and read-only system inspection tools.
F. Add permission broker, explicit approval workflows and audited system changes.
G. Add secure Community Grid enrollment and remote workload management.
H. Package as a bootable Debian-based image.

## Out of scope for the first milestone
Unrestricted root shell, autonomous unattended OS upgrades, remote arbitrary code execution, automatic network exposure, guaranteed GPU support on Raspberry Pi, or claims that distributed RAM behaves like local RAM.

## Validation
Synthetic profiles for x86 and ARM, low-memory and unsupported GPU cases; offline first boot; failed downloads; malicious tool arguments; rollback; model unavailable; unauthorized remote operations; model benchmark reproducibility.
