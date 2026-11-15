# CGOS 0.1 implementation plan

Start with an amd64 Debian Trixie hybrid ISO that boots to a console, starts cgosd, and runs a known local graph. Keep the first image small. Package graph execution and diagnostics before adding the installed image and opt-in remote scheduling. See the [architecture](OS_ARCHITECTURE.md) and [build design](BUILD_SYSTEM.md).

| Milestone | Deliverable and exit test |
| --- | --- |
| 0.1-M1 | Add a minimal live-build profile, a systemd cgosd service and a console client. Build in a locked disposable Linux builder. Boot in BIOS and UEFI VMs, verify service readiness and volatile live state, and execute normalize → SHA-256 through the local service API. Compare the output and artifact digest with an independent reference, export manifest and serial evidence, and shut down cleanly. |
| 0.1-M2 | Compose the VM disk image with isolated persistent state and first-boot identity. Verify state and identity across restart, service resource enforcement, crash recovery, backup and rollback. Compare repeat image builds and report any remaining reproducibility differences. |
| 0.1-M3 | Complete the Linux SSH isolation preflight, then exercise operator-approved enrollment and a two-node reference graph in isolated VMs. Verify denial for an unenrolled identity and exercise offline recovery, deadline fencing and allowed local placement during a disconnect. |
| 0.1 release gate | Publish verified artifacts, manifests and the hardware evidence set. A separately approved Dell live boot checks the firmware mode, actual CPU and device inventory, console, NIC and service startup while confirming internal SATA remains unmounted. Installation is a separate decision after identifying the exact target disk. |

M1 dependencies are an approved disposable Debian build environment, the current live-build tools, QEMU and UEFI firmware for VM boot checks, and the Phase 1A/1B source and tests. The Lenovo preflight found no usable Linux VM or container. Confirm an approved runner before implementation builds, and obtain approval before downloading or installing tools. Scope M1 to the local graph and diagnostics. Full desktop, accelerated inference, image installation, and physical distributed execution are outside M1.

The main risks are build drift from mutable packages, BIOS/UEFI differences, loss of live RAM evidence, and remote isolation that is still unverified on Linux. Pin and compare build inputs, test both firmware paths, provide explicit evidence export and keep remote service activation opt-in behind its validation gate. Preserve the archive of existing CGOS experiments throughout.
