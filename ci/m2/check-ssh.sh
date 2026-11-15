#!/bin/bash
set -euo pipefail
export LC_ALL=C
secret=$(mktemp /run/cgos-test-private/unknown.XXXXXX)
trap 'rm -f "$secret"' EXIT
install -m 0600 /run/cgos-test-private/media/unknown "$secret"
probe='{"version":2,"action":"probe"}'
base=(ssh -F /dev/null -p 22227 -o BatchMode=yes -o IdentitiesOnly=yes
      -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=2)
for n in A B; do
    if [[ $n == A ]]; then ip=10.44.0.1; else ip=10.44.0.2; fi
    target=cgos-worker@$ip
    enrolled=("${base[@]}" -i /etc/cgos/distributed/client -o UserKnownHostsFile=/run/cgos-test-private/media/known_hosts)
    printf '%s' "$probe" | timeout 5 "${enrolled[@]}" -T "$target" | python3 -c \
        'import json,sys; d=json.load(sys.stdin); assert d["version"]==2 and d["node"]==sys.argv[1] and d["slots"]==1; print(d)' "$n"
    deny() {
        name=$1
        pattern=$2
        shift 2
        set +e
        out=$(printf '%s' "$probe" | timeout 5 "$@" 2>&1)
        code=$?
        set -e
        if (( code == 0 || code == 124 )); then echo "$name unexpected result $code"; return 1; fi
        if [[ $pattern != - ]]; then printf '%s\n' "$out" | grep -Eq "$pattern"; fi
        echo "$n $name rejected ($code)"
        printf '%s\n' "$out"
    }
    deny unknown 'Permission denied \(publickey\)' "${base[@]}" -o UserKnownHostsFile=/run/cgos-test-private/media/known_hosts -i "$secret" -T "$target"
    deny host-pin 'Host key verification failed' "${base[@]}" -o UserKnownHostsFile=/run/cgos-test-private/media/wrong_hosts -i /etc/cgos/distributed/client -T "$target"
    deny shell '-' "${enrolled[@]}" -T "$target" true
    deny pty 'PTY allocation request failed' "${enrolled[@]}" -tt "$target"
    deny forwarding 'remote port forwarding failed' "${enrolled[@]}" -N -R 0:127.0.0.1:22 -o ExitOnForwardFailure=yes "$target"
    deny sftp 'subsystem request failed' "${enrolled[@]}" -s "$target" sftp
done
systemctl start cgos-coordinator.service
systemctl is-active cgos-coordinator.service
timeout 35 sh -c 'until test -S /run/cgos-coordinator/api.sock; do sleep .2; done'
cgos --socket /run/cgos-coordinator/api.sock diagnostics
