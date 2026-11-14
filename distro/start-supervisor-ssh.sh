#!/bin/sh
# Run in the Dell live session after confirming the Ethernet link and key.
set -eu

if [ "$#" -ne 1 ] || [ "$(id -u)" -ne 0 ]; then
    echo "Usage: sudo $0 /path/to/lenovo-public-key.pub" >&2
    exit 2
fi

ip -o -4 address show | awk '$4 == "192.168.50.2/24" { found++ } END { exit (found != 1) }' || {
    echo 'Assign 192.168.50.2/24 to the confirmed Dell Ethernet interface first.' >&2
    exit 1
}

account=grid-supervisor
state=/run/community-grid-ssh
if [ -e "$state" ]; then
    echo 'Supervisor SSH was already provisioned this boot. Inspect its service or reboot to reset.' >&2
    exit 1
fi
if getent passwd "$account" >/dev/null; then
    echo "Account $account already exists. Use a clean live session." >&2
    exit 1
fi

key=$(awk 'NR == 1 && $1 == "ssh-ed25519" && $2 ~ /^[A-Za-z0-9+\/=]+$/ {print $1, $2}
    END {if (NR != 1) exit 1}' "$1")
test -n "$key"
install -d -m 0755 -o root -g root "$state" /run/sshd
printf 'restrict,from="192.168.50.1" %s\n' "$key" > "$state/authorized_keys"
chmod 0644 "$state/authorized_keys"
printf '%s\n' "$key" > "$state/lenovo.pub"
ssh-keygen -lf "$state/lenovo.pub"

useradd --system --no-create-home --home-dir "$state" --shell /bin/sh "$account"
ssh-keygen -q -t ed25519 -N '' -f "$state/host_ed25519"
cat > "$state/sshd_config" <<EOF
AddressFamily inet
ListenAddress 192.168.50.2
Port 22
HostKey $state/host_ed25519
PidFile $state/sshd.pid
AuthorizedKeysFile $state/authorized_keys
AllowUsers $account@192.168.50.1
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
UsePAM yes
DisableForwarding yes
PermitTTY no
PermitUserRC no
EOF
/usr/sbin/sshd -t -f "$state/sshd_config"
systemd-run --unit=community-grid-supervisor-ssh --property=Type=exec \
    /usr/sbin/sshd -D -e -f "$state/sshd_config"
ssh-keygen -lf "$state/host_ed25519.pub"
echo "Copy only $state/host_ed25519.pub to the Lenovo. Record its fingerprint from this console."
