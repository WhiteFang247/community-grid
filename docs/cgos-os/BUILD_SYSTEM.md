# CGOS build system

Extend the existing Debian Trixie live-build path. The current distro/build-iso.sh already composes an amd64 hybrid ISO and the workflow inspects the filesystem for SSH credentials and publishes SHA256SUMS. It currently uses mutable archive mirrors and a rolling builder tag. Replace those inputs with explicit locks. Keep the existing preview workflow independent while the new profile is validated.

| Option | Decision |
| --- | --- |
| Debian live-build | Keep for ISO, with a small console profile and shared root filesystem overlay. Retains the tested boot and package base. |
| Debian installer or image composer | Use a separate VM-only image composition job for the installable disk artifact. Prototype with debootstrap and a userspace image composer such as debos, subject to tool approval. Reuse the package lock, overlay and service configuration. |
| Buildroot or Yocto | Defer. Neither is needed to validate x86 boot or graph services, and both require a new package and update pipeline. |

Build a clean source revision in a disposable Linux builder pinned by digest. Verify Debian archive signatures, pin the snapshot timestamp and package versions, lock builder packages and CI actions, record SOURCE_DATE_EPOCH, normalize file ordering and timestamps where supported, and hold image geometry and filesystem creation inputs constant. Keep a deliberate process for refreshing the snapshot and security patches. Pin every external source by digest or signed revision.

Emit two independently bootable artifacts, a hybrid ISO and a partitioned raw disk image, plus SHA256SUMS and a signed release manifest. The disk image is initially for disposable VM disks. Its partition map and supported firmware modes are shipping inputs. Give each installed instance fresh machine and SSH identity on first boot. Exclude credentials, machine identities, user data, and build caches from both artifacts.

The manifest records schema version, source revision and tree digest, builder digest, archive snapshots and signature verification, package names, versions and hashes, external source IDs, kernel package and config digest, initramfs and bootloader settings, runtime and service config digests, image geometry, artifact hashes and sizes, and test results. Export the package inventory in machine-readable form. Repeat clean builds from the same inputs and compare image hashes. If bytes differ, localize the difference to boot metadata, SquashFS, filesystem metadata, or payload. Report the difference and avoid claiming byte reproducibility until the comparison passes.

CI gates run existing tests, inspect artifact contents and verify hash manifests, then boot the ISO and disk image in BIOS and UEFI VM modes as supported. Use bounded serial readiness, verify the root source, test a local graph against a reference output, confirm inbound SSH defaults to off, inspect health and logs, and shut down. Reboot a disposable installed VM to check state and identity. Exercise image and state rollback in the VM. Save manifests and serial logs alongside the checksums. Initial dependencies are an approved Linux builder, Debian live-build tools, QEMU and UEFI firmware for CI, and approved disk composition tooling for the later image.
