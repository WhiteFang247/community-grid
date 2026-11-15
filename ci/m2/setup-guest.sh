#!/bin/sh
set -eu
phase=identity
trap 'rc=$?; if test "$rc" -ne 0; then echo "M2_SETUP_FAILED phase=$phase rc=$rc"; ip -br address || true; ip route || true; systemctl --failed --no-pager || true; fi' EXIT
export LC_ALL=C SYSTEMD_COLORS=0 SYSTEMD_PAGER=cat
role=$(cat /run/cgos-test-private/media/role)
echo "ROLE=$role"
uname -a
cat /etc/os-release /etc/issue /proc/cmdline /proc/sys/kernel/random/boot_id
test "$(findmnt -n -o FSTYPE /)" = overlay
test "$(findmnt -n -o FSTYPE --mountpoint /run/live/medium)" = iso9660
findmnt / /run/live/medium 2>/dev/null || true
test "$(nproc --all)" -eq 2
awk '/^MemTotal:/ {exit !($2>1400000 && $2<1600000)}' /proc/meminfo
mode=bios; test ! -d /sys/firmware/efi || mode=uefi
echo "FIRMWARE=$mode"
timeout 45 systemctl is-system-running --wait
systemctl is-active cgos.service
timeout 30 sh -c 'until test -S /run/cgos/api.sock; do sleep .2; done'
cgos diagnostics
phase=default-services
for unit in cgos-worker cgos-worker-relay cgos-coordinator ssh.service ssh.socket; do
    if systemctl is-active --quiet "$unit"; then echo "Unexpected active default $unit"; exit 1; fi
done
phase=configuration-absent
test ! -d /etc/cgos/distributed
phase=network-interface
iface=$(ls /sys/class/net | grep -vx lo)
test "$(printf '%s\n' "$iface" | wc -l)" -eq 1
case "$role" in A) address=10.44.0.1;; B) address=10.44.0.2;; *) exit 1;; esac
phase=isolated-network
ip link set "$iface" up
ip addr add "$address/30" dev "$iface"
ip -br address
ip route
test -z "$(ip -4 route show default)"
test -z "$(ip -6 route show default)"
phase=stage-credentials
install -d -m 0755 /etc/cgos/distributed
c=/etc/cgos/distributed
printf 'CGOS_NODE=%s\nCGOS_COORDINATOR=A\n' "$role" > "$c/worker.env"
install -m 0600 /run/cgos-test-private/media/host "$c/host"
{ printf 'restrict,from="10.44.0.1" '; cat /run/cgos-test-private/media/client.pub; } > "$c/authorized_keys"
chmod 0644 "$c/authorized_keys"
cat > "$c/sshd_config" <<EOF
Port 22227
ListenAddress $address
HostKey $c/host
AuthorizedKeysFile $c/authorized_keys
AllowUsers cgos-worker@10.44.0.1
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
StrictModes yes
DisableForwarding yes
PermitTTY no
PermitUserRC no
PermitUserEnvironment no
UsePAM yes
LoginGraceTime 5
MaxSessions 1
ForceCommand /usr/local/libexec/cgos-worker-relay
EOF
phase=worker-start
systemctl start cgos-worker.service cgos-worker-relay.service
phase=worker-readiness
systemctl is-active cgos-worker.service cgos-worker-relay.service
timeout 15 sh -c 'until ss -ltn | grep -Fq ":22227 "; do sleep .2; done'
phase=effective-ssh-policy
/usr/sbin/sshd -T -f "$c/sshd_config" -C user=cgos-worker,addr=10.44.0.1,host=guest \
  | grep -E '^(listenaddress|allowusers|authenticationmethods|forcecommand|disableforwarding|permittty|permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowagentforwarding|allowtcpforwarding|x11forwarding) '
if test "$role" = A; then
    install -o cgos-coordinator -g cgos-coordinator -m 0600 /run/cgos-test-private/media/client "$c/client"
    install -m 0644 /run/cgos-test-private/media/nodes.json "$c/nodes.json"
fi
echo "M2_GUEST_READY_$role"
