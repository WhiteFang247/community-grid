# CGOS 0.1-M1 acceptance record

**Accepted on November 15, 2026.** Both disposable VM guests booted the same
published ISO and passed the native runtime acceptance checks.

| Item | Verified identity |
| --- | --- |
| M1 build source | `55efca4f3a0995337f40f50428946dc0b6ed83dd` |
| ISO build | [43684773526](https://github.com/WhiteFang247/community-grid/actions/runs/43684773526) |
| Original ZIP | [Artifact 14150475330](https://github.com/WhiteFang247/community-grid/actions/runs/43684773526/artifacts/14150475330) |
| ZIP SHA-256 | `9e0b4f0124f6a4e08bc6c34ebbc5a673ee8e09d7c64f18af1cd1e18782300226` |
| ISO SHA-256 | `7da3524dcce941001f0caf5eeb2ae812ecb2c81fe97d8512d57c13bc8418c4ab` |
| Acceptance harness | `2cd17ba6d3ea1bbf7b9c6bddd11f264485b52678` |
| VM acceptance | [43696876508](https://github.com/WhiteFang247/community-grid/actions/runs/43696876508) |

- [BIOS evidence](https://github.com/WhiteFang247/community-grid/actions/runs/43696876508/artifacts/14155246347):
  SeaBIOS, actual BIOS boot, full acceptance in 68.6 seconds.
- [UEFI evidence](https://github.com/WhiteFang247/community-grid/actions/runs/43696876508/artifacts/14155420005):
  OVMF, actual UEFI boot, full acceptance in 88.2 seconds.

Both jobs verified the original ZIP and ISO, checked 11 build-input digests
against the source commit, and booted Debian 13.7 Live with an overlay root
and a read-only virtual CD-ROM. Both confirmed systemd service and Unix socket
readiness, executed normalize and hash, checked the final result independently,
and recovered the same completed graph and artifact after restarting the daemon.

- Graph result: `679c91f59c78c514abe304e8a215351b7ad9a190d1b6e025260e54a599e91ed8`
- Artifact SHA-256: `f95b10a11a618ba873c7bdcf675d7a3f4e8bd70997ff309010fbb67ba7eb552b`

Acceptance covers this exact image. There was no physical CGOS boot, no
multi-node CGOS runtime validation, no full-reboot persistence test, and no
byte-for-byte rebuild guarantee. Live state survived a daemon restart in the
same boot session. Debian repository and toolchain inputs can change.

Keep the original ZIP, image, checksums, build manifests and VM evidence together
when archiving. Actions artifact links are subject to retention, and a rebuild
must use a new artifact identity and checksum. Do not replace the accepted image.
