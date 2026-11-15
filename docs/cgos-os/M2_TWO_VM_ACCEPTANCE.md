# CGOS M2 two-guest acceptance

Use the Task 5 branch workflow to build an independent Debian live image
and run the guest acceptance job from the same workflow run. The second
job checks source identity, ZIP digest, build-input manifest and ISO SHA-256
before boot. The M1 release is independent. Build locally in a disposable
Trixie builder with bash distro/build-iso.sh m2. The resulting files appear
in .build/cgos-m2.

The default ISO starts cgos.service with local Unix-socket execution.
Distributed services are deliberately disabled. Provision the following
files under the administrator-owned /etc/cgos/distributed directory:

- worker.env defines CGOS_NODE and CGOS_COORDINATOR.
- sshd_config defines a dedicated worker account and explicitly scoped
  ListenAddress, public-key-only authentication, a restricted source and
  the fixed /usr/local/libexec/cgos-worker-relay command.
- The worker host key and authorized public keys are provisioned out of
  band. Do not copy them into live-build inputs.
- nodes.json and a coordinator identity are only needed on the coordinator
  guest. See M2_ENROLLMENT.md for the versioned manifest and trust rules.

Start the worker and relay, verify the effective SSH restrictions and
review the host pins before starting cgos-coordinator. Its Unix socket
is /run/cgos-coordinator/api.sock. The coordinator and worker use separate
service accounts and state directories. The relay drops to the worker
account for the fixed command. Stopping the coordinator retains SQLite
assignments in that boot. Workers retain terminal rows and cancellation
tombstones in their own state. Full guest reboot is outside this live
persistence guarantee.

ci/m2 loads ephemeral provisioning media into each isolated guest and
validates the expected BIOS/UEFI roles, unique kernel boot IDs and
separate live overlays. Host QMP controls the private virtual link for
offline trials. A transport barrier records admission before dropping
the acknowledgment, closing the virtual link or delaying a submission.
The original ISO is mounted read-only. No host network forwarding or
physical drive is passed through. CI saves the console and trial evidence
with credentials kept outside the artifact.

The exact matrix is five excluded warmups, 30 healthy graphs, 30 local
baselines and 20 each for lost acknowledgment, process restart, offline
recovery and cancellation. A failed barrier is preserved and ends the
matrix without treating unrun cases as passes. Failed gates require
review. KVM and TCG runs must be reported separately.
