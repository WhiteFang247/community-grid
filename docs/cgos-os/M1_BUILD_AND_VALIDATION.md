# CGOS 0.1-M1 build and validation

M1 is accepted for the exact published ISO. See the [acceptance record](M1_ACCEPTANCE.md) for the verified source, checksums, passing BIOS and UEFI results, and remaining limitations.

## Source and build

The existing distro/build-iso.sh entry point gains an m1 profile. The default preview profile retains its existing packaging. The M1 profile includes the stock Debian amd64 kernel and its in-tree drivers, live-boot, systemd, Python, a console CLI and minimal diagnostics. It configures Syslinux and GRUB EFI. The runtime imports only the graph engine and the existing durable worker protocol. It runs allowlisted local text operations in-process and persists attempt and graph state in separate SQLite files. The systemd unit uses a dynamic unprivileged user, resource limits and Unix sockets only.

Use an approved disposable Debian Trixie amd64 builder with git, live-build, debootstrap, xorriso, squashfs-tools, ca-certificates and the live-build dependencies for both selected bootloaders. Use a fresh WORK directory and the complete isolated source tree, including the Phase 1A and 1B files. Record a clean revision for release. The build records HEAD, working tree status and hashes of staged inputs so a development build can be traced to the actual source.

    bash -n distro/build-iso.sh distro/m1/build.sh
    sudo bash distro/build-iso.sh m1

Run a separate syntax check for each shell file, including the launcher and chroot hook. With default WORK, the ISO and SHA256SUMS will appear in .build/cgos-m1. The sibling manifest directory records the source commit and status, build-input digests, effective live-build configuration, builder and image package versions, and kernel configuration. Verify the ISO with sha256sum -c SHA256SUMS from that work directory. The Debian mirrors are mutable. Snapshot and toolchain locking and a repeat-build digest comparison are pending. This profile does not claim byte reproducibility.

## VM acceptance

Use a disposable VM with no internal host disk attached. Run once in BIOS mode and once with UEFI firmware. Capture firmware mode, boot source, service status, journal and command output. If using a serial console, explicitly add a serial kernel console in the boot menu. Run as root or use sudo from the live console because M1 restricts the socket to the service user and root.

    sudo systemctl is-active cgos.service
    sudo cgos diagnostics
    sudo cgos graph submit /usr/share/cgos/reference-graph.json
    sudo cgos graph status m1-reference
    sudo cgos graph result m1-reference hash
    sudo systemctl restart cgos.service
    sudo cgos graph result m1-reference hash

Poll status with a 30-second bound until both units complete. Independently hash the UTF-8 bytes of seed library for the expected value. Hash the JSON string encoding of that hex result, including quotes and without a trailing newline, for the artifact digest. Compare both values after restart. Save the logs before shutting down because the live overlay is volatile. Confirm the daemon emits service_stopped and the VM powers off cleanly. On a future persistent root the systemd state directory survives machine restarts as well.

## Dell checklist

1. Use the prior i5-2500 and roughly 15.58 GiB RAM inventory as a historical baseline. Confirm the actual GPU, NIC, firmware mode, and device identifiers at the intended boot. The core uses the baseline amd64 target.
2. After image verification and separate approval, identify any USB target by physical device and serial before writing. Recheck the finished USB by reading back the ISO-length region.
3. With approval, use F12 to select the USB for one boot. Capture boot source and firmware mode and verify internal drives remain unmounted. Run the VM acceptance commands and export evidence to approved storage.
4. Shut down, remove the USB and select the existing system for rollback. Any eventual installation requires an installer or disk image, independent disk serial and partition-map verification, a tested backup, and separate explicit authorization for the exact disk.

The next action is an approved Debian build followed by the BIOS and UEFI VM acceptance checks. Remote enrollment, installed-image rollback and the desktop are outside M1.

## GitHub build and artifact verification

Push the reviewed `cgos/0.1-m1` branch with a normal fast-forward push. Its build
workflow runs Python tests and shell syntax checks in a disposable Debian
Trixie container, checks for BIOS and UEFI El Torito entries, and publishes the
ISO, SHA256SUMS and manifest for the exact commit. The boot catalog check is
static and does not substitute for a boot test.

For each VM, attach only the published ISO as a virtual CD-ROM. Use fresh
virtual machine state for each run. Use SeaBIOS for the legacy run and OVMF
with a disposable copy of its variables image for the UEFI run. No virtual
hard disk is required. Check `test -d /sys/firmware/efi` in each guest to record
the actual boot mode. Follow the graph and restart procedure above and save the
guest evidence with the ISO digest and firmware mode.
