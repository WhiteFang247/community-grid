# CGOS 0.1 implementation plan

Start with an amd64 Debian Trixie hybrid ISO that boots to a console, starts cgosd, and runs a known local graph. Keep the first image small. Package graph execution and diagnostics before adding the installed image and opt-in remote scheduling. See the [architecture](OS_ARCHITECTURE.md) and [build design](BUILD_SYSTEM.md).

| Milestone | Deliverable and exit test |
| --- | --- |
| 0.1-M1 | Accepted. See the [verified ISO and VM evidence](M1_ACCEPTANCE.md). |
| 0.1-M2 | Run one verified graph across two independently booted CGOS guests with explicit enrollment, bounded retries and auditable placement. See the [design and acceptance proposal](M2_DISTRIBUTED_PLAN.md). |
| Later milestone | Prove first-boot identity and persistent state across machine restarts, backup and rollback, and repeat-build reproducibility. Scope and sequencing remain open. |
| 0.1 release gate | Publish verified artifacts, manifests and the hardware evidence set. A separately approved Dell live boot checks the firmware mode, actual CPU and device inventory, console, NIC and service startup while confirming internal SATA remains unmounted. Installation is a separate decision after identifying the exact target disk. |

M1 covers the local graph and diagnostics. M2 targets isolated virtual nodes. Full desktop, accelerated inference, image installation and physical distributed execution require separate plans.

The main risks are build drift from mutable packages, BIOS/UEFI differences, loss of live RAM evidence, and remote isolation that is still unverified on Linux. Pin and compare build inputs, test both firmware paths, provide explicit evidence export and keep remote service activation opt-in behind its validation gate. Preserve the archive of existing CGOS experiments throughout.
