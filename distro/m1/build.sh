# Sourced by build-iso.sh on a disposable Debian builder.
profile="${CGOS_PROFILE:-m1}"
if [[ -e config || -e chroot || -e binary || -e live-image-amd64.hybrid.iso ]]; then
  echo "Use a fresh WORK directory for $profile" >&2
  exit 1
fi
lb config --distribution trixie --architectures amd64 --binary-images iso-hybrid \
  --bootloaders "syslinux grub-efi" --linux-flavours amd64 \
  --debian-installer none --apt-recommends false \
  --bootappend-live "boot=live components username=user hostname=cgos" \
  --iso-volume "CGOS 0.1-${profile^^}" \
  --mirror-bootstrap https://deb.debian.org/debian/ \
  --mirror-chroot https://deb.debian.org/debian/ \
  --mirror-binary https://deb.debian.org/debian/
stage=config/includes.chroot
install -Dm644 "$ROOT/distro/m1/packages.list.chroot" config/package-lists/cgos.list.chroot
install -Dm644 "$ROOT/distro/m1/cgos.service" "$stage/etc/systemd/system/cgos.service"
install -Dm755 "$ROOT/distro/m1/cgos" "$stage/usr/local/bin/cgos"
install -Dm755 "$ROOT/distro/m1/0100-cgos.hook.chroot" config/hooks/live/0100-cgos.hook.chroot
install -Dm644 "$ROOT/distro/m1/reference-graph.json" "$stage/usr/share/cgos/reference-graph.json"
modules=(__init__ graph physical native)
if [[ "$profile" == m2 ]]; then
  modules+=(enrollment worker_v2)
  install -Dm644 "$ROOT/distro/m2/packages.list.chroot" config/package-lists/cgos-m2.list.chroot
  install -Dm755 "$ROOT/distro/m2/0200-cgos-m2.hook.chroot" config/hooks/live/0200-cgos-m2.hook.chroot
  install -Dm644 "$ROOT/distro/m2/reference-graph.json" "$stage/usr/share/cgos/m2-reference-graph.json"
  for unit in cgos-coordinator cgos-worker cgos-worker-relay; do
    install -Dm644 "$ROOT/distro/m2/$unit.service" "$stage/etc/systemd/system/$unit.service"
  done
  for command in cgos-worker-relay cgos-check-worker-sshd; do
    install -Dm755 "$ROOT/distro/m2/$command" "$stage/usr/local/libexec/$command"
  done
fi
for module in "${modules[@]}"; do
  install -Dm644 "$ROOT/runtime/community_grid/$module.py" "$stage/opt/cgos/runtime/community_grid/$module.py"
done
mkdir -p manifest
git -C "$ROOT" rev-parse HEAD > manifest/source-commit.txt
git -C "$ROOT" status --porcelain --untracked-files=all > manifest/source-status.txt
(
  cd "$ROOT"
  inputs=(distro/build-iso.sh distro/m1/*)
  if [[ "$profile" == m2 ]]; then inputs+=(distro/m2/*); fi
  for module in "${modules[@]}"; do inputs+=("runtime/community_grid/$module.py"); done
  sha256sum "${inputs[@]}"
) > manifest/build-inputs.SHA256SUMS
cp config/{common,bootstrap,chroot,binary} manifest/
dpkg-query -W live-build debootstrap xorriso squashfs-tools > manifest/builder-packages.txt
lb build
test -s live-image-amd64.hybrid.iso
dpkg-query --admindir="$PWD/chroot/var/lib/dpkg" -W > manifest/image-packages.txt
for kernel_config in chroot/boot/config-*; do
  cp "$kernel_config" manifest/
done
sha256sum live-image-amd64.hybrid.iso > SHA256SUMS
echo "CGOS image built in $WORK"
