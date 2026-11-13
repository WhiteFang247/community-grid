# Dell OptiPlex 990 SFF — first live-boot test

**Status:** experimental live ISO build recipe; not yet validated on the Dell. This is a **live-only image**, not a hard-disk installer. It does not intentionally write to internal disks.

## Hardware expectations
- Dell OptiPlex 990 SFF uses legacy BIOS on many configurations; verify the USB appears in the F12 boot menu.
- The i5-2500 supports x86-64; this image targets amd64.
- XFCE is selected for relatively low resource use. CPU-only local inference is the expected starting point.
- GPU acceleration, secure boot, Wi-Fi firmware and peripherals are not guaranteed.

## Build / download
1. In GitHub Actions, manually run **Build Community Grid OS live ISO (experimental)** on `feature/community-grid-os`.
2. Wait for a successful workflow. Download its `community-grid-os-amd64-live` artifact and extract the ISO.
3. Verify the ISO checksum against `SHA256SUMS` before writing to USB.
4. On a separate machine, flash the ISO to a **spare USB stick** with a trusted image-writing tool. **This erases the selected USB device.** Double-check the target.
5. Keep the Dell's existing system drive untouched. Use F12 to select the USB for a temporary boot.

## Test
- Confirm XFCE desktop appears and network works.
- Launch **Community Grid OS** from the application menu.
- Open `http://127.0.0.1:8765/api/health`; expect `{"status":"ok","mode":"standalone","version":"0.1.0"}`.
- Inspect the hardware and offline model recommendation. No LLM is downloaded or run in this preview.
- Verify local system remains usable with Ethernet unplugged.
- Shut down and remove the USB; verify the original OS still boots.

## Known limitations
No installed OS image, no chat assistant, no model installer, no authenticated cluster joining, no actual distributed jobs, no automatic driver setup, no GPU performance guarantees. The dashboard is a localhost-only read-only preview.

## Troubleshooting
If the ISO build fails, retain the full GitHub Actions log and avoid writing an unverified image. If the Dell fails to boot, restore the original boot order rather than repartitioning any internal drive.

## Collecting a first-boot diagnostic report
Open a terminal in the live desktop and run:

```bash
PYTHONPATH=/opt/community-grid/runtime python3 -m community_grid.diagnostics > ~/community-grid-diagnostics.json
```

The report contains hardware, network interface, route and disk metadata, not file contents or credentials. **Review it before sharing**: MAC addresses, local IP addresses, hostnames and disk identifiers may be included. It is safe to copy to the Lenovo after review.

## Codex supervisor over direct Ethernet
- Plug a USB-A 3.0 Gigabit Ethernet adapter into the Lenovo, and connect its RJ45 port directly to the Dell's Ethernet port with a standard Cat5e/Cat6 cable.
- Assign static IP addresses on that Ethernet link only (example: Lenovo `192.168.50.1/24`, Dell `192.168.50.2/24`). Leave Lenovo Wi-Fi for internet access.
- **SSH access is not automatically enabled or authorized in this preview.** For unattended Codex access, a later image must include an explicitly provisioned SSH server and a trusted Lenovo public key, with no default password.
- Never expose the dashboard or SSH to a public interface without authentication and firewall policy. Do not automatically grant Codex root access.
