#!/usr/bin/env bash
set -euo pipefail
# Run on Debian Trixie amd64 as root in a disposable build environment.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${WORK:-$ROOT/.build/community-grid-live}"
if [[ "$(id -u)" != 0 ]]; then echo "Run as root on a disposable Debian build host" >&2; exit 1; fi
command -v lb >/dev/null || { echo "Install live-build first" >&2; exit 1; }
mkdir -p "$WORK"
cd "$WORK"
lb config --distribution trixie --architectures amd64 --binary-images iso-hybrid \
  --debian-installer none --bootappend-live "boot=live components username=user" \
  --archive-areas "main contrib non-free-firmware" --mirror-bootstrap http://deb.debian.org/debian/ \
  --mirror-chroot http://deb.debian.org/debian/ --mirror-binary http://deb.debian.org/debian/
mkdir -p config/package-lists config/includes.chroot/opt/community-grid/runtime \
  config/includes.chroot/etc/systemd/system \
  config/includes.chroot/usr/share/applications config/hooks/live
cp "$ROOT/distro/packages.list.chroot" config/package-lists/community-grid.list.chroot
cp -a "$ROOT/runtime/community_grid" config/includes.chroot/opt/community-grid/runtime/
install -Dm600 "$ROOT/distro/community-grid-wired.nmconnection" \
  config/includes.chroot/etc/NetworkManager/system-connections/community-grid-wired.nmconnection
cp "$ROOT/distro/community-grid.service" config/includes.chroot/etc/systemd/system/community-grid.service
cp "$ROOT/distro/community-grid.desktop" config/includes.chroot/usr/share/applications/community-grid.desktop
install -Dm755 "$ROOT/distro/start-supervisor-ssh.sh" config/includes.chroot/usr/local/sbin/start-supervisor-ssh
cp "$ROOT/distro/0100-community-grid.hook.chroot" config/hooks/live/0100-community-grid.hook.chroot
chmod +x config/hooks/live/0100-community-grid.hook.chroot
lb build
echo "ISO built in $WORK; check live-image-amd64.hybrid.iso"
