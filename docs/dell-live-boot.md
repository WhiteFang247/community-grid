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
