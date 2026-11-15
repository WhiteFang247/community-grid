# Hardware target

The initial target is the Dell OptiPlex 990 SFF. Historical evidence in experiments/dell-live-20261114T105513Z recorded a live USB session with an Intel Core i5-2500, four cores and about 15.58 GiB usable RAM. Experiment 2 verified a Sandy Bridge inference backend. Treat these as historical baselines and record the same inventory on the intended image. The core runtime needs no inference backend.

| Area | Verify before installation |
| --- | --- |
| Firmware | Record BIOS revision, F12 boot options, boot mode from /sys/firmware/efi, SATA controller mode and relevant firmware settings. Test the intended BIOS or UEFI path from USB in a separately approved boot. |
| CPU | Record lscpu output and flags. Build baseline amd64 packages and verify any optional AVX path at runtime. Avoid an AVX2 requirement. |
| RAM | Compare firmware inventory, Linux MemTotal and service cgroup limits. Record capacity, boot idle usage and graph peak RSS. |
| Storage | Inspect lsblk and findmnt read-only. Record device model, serial or WWN, exact size, transport, mount points, partition table and boot media. Confirm backups and verify the intended physical target independently before any separate destructive approval. |
| Network | Record PCI IDs, kernel driver and link state with lspci -nnk, ip and ethtool if available. Confirm the active lease, direct-link route, MTU and peer address. Treat previous ICS addresses as historical. |
| Graphics | Identify the active GPU and driver with lspci -nnk and DRM sysfs. Check console output in both supported firmware paths and acceleration only if hardware and drivers expose it. |
| Thermals and power | Capture available hwmon readings, fan and throttling indicators during idle and a bounded load. Use an external power meter if system power counters are absent. Record ambient conditions and tool coverage. |

The first physical exercise, pending separate authorization, boots live media with internal storage unmounted. Recheck boot source, mounts and output paths before exporting anything. This specification makes no assumption about the current disk layout or firmware configuration.
