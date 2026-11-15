# CGOS OS architecture

CGOS 0.1 is an independently bootable amd64 Linux distribution. Its system runtime gives applications a local computational graph interface and schedules declared operations on local or explicitly enrolled workers. Debian supplies the kernel, drivers and base userspace. CGOS owns image composition, graph semantics, worker admission and system policy.

| Area | Required for 0.1 | Later research |
| --- | --- | --- |
| Boot | Hybrid read-only ISO with BIOS and UEFI paths. Bootloader loads Linux and initramfs. Live boot finds SquashFS and adds a volatile overlay. Systemd brings up the runtime and diagnostics. | Signed boot chain and measured boot |
| Installed image | GPT with ESP, BIOS boot partition if legacy GRUB is supported, root filesystem and separate state filesystem. Verify both firmware paths in VMs before selecting a Dell boot mode. | Signed A/B system partitions |
| Kernel and userspace | Debian stable amd64 kernel, systemd, udev, Python 3, SQLite, iproute2, DHCP client or network manager, and minimal console diagnostics. Load in-tree drivers through udev. Add firmware only for identified hardware. | Custom kernel configuration where measurements justify it |
| CPU and GPU | Linux scheduling and cgroup v2 enforce local limits. Report effective CPU and memory available to the service. Graph placement uses declared requirements and worker capacity. Report GPUs as inventory until an operation has a verified backend. | NUMA, accelerator quotas and heterogeneous execution |
| Memory and storage | Bound queues, inputs and artifacts. Use SQLite for authoritative local metadata and content-addressed artifacts. Keep installed state under /var/lib/cgos. Live mode uses volatile state and explicit export to approved media. | Distributed replication and checkpoint migration |
| Networking | Wired DHCP, optional explicitly selected static link, operator-pinned worker identity and short connection deadlines. A valid local placement works without a network. Apply reachability and RTT as placement inputs after capability filtering. | Discovery, adaptive placement, certificate rotation automation |
| Desktop | Console diagnostics and optional loopback dashboard. | Desktop shell and interactive graph workloads |
| Observability | Structured journal records and an explicit evidence export with image ID, node ID, graph and attempt IDs, placement and timing. | Fleet telemetry with operator-controlled data export |
| Updates | Version full images and schema. Boot the previous verified image for system rollback and restore its matching state snapshot if a migration changed compatibility. | Unattended signed updates with automatic rollback |

The application SDK starts with a versioned schema and Python client for graph submission, status, cancellation and verified results over a local Unix socket. Resource declarations describe capabilities and limits. Applications never supply executable commands or control host scheduling primitives. Odysseus owns prompts, models, orchestration and UX. It uses the same SDK and declared operations as other applications. The core can boot and run its reference graph without any AI dependency.

See the [build design](BUILD_SYSTEM.md), [native runtime](NATIVE_RUNTIME.md) and [release plan](CGOS_0.1_PLAN.md).
