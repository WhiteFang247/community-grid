#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
evidence="$PWD/evidence"
mkdir -p "$evidence"
stage=$(mktemp -d)
auth=$(sudo mktemp -d /run/cgos-ci-auth.XXXXXXXX)
sudo chmod 755 "$auth"
port=22227
account=cgos-ci-relay
chmod 755 "$stage"
cleanup() {
    if test -s "$stage/sshd.pid"; then sudo kill "$(cat "$stage/sshd.pid")" || true; fi
    if sudo test -s "$stage/worker/pid"; then sudo kill "$(sudo cat "$stage/worker/pid")" || true; fi
    if test -s "$stage/sshd.log"; then sudo cat "$stage/sshd.log" > "$evidence/sshd.log"; fi
    if test -n "${SSH_AGENT_PID:-}"; then ssh-agent -k >/dev/null; fi
    sudo rm -rf -- "$stage" "$auth"
}
trap cleanup EXIT
sudo useradd -M -s /bin/sh "$account"
sudo install -d -m 0755 /run/sshd
mkdir "$stage/runtime"
cp -r runtime/community_grid "$stage/runtime/"
sudo install -d -m 0700 -o "$account" "$stage/worker"
for name in host coordinator unknown wrong-host; do
    ssh-keygen -q -t ed25519 -N '' -f "$stage/$name"
done
(printf 'restrict,from="127.0.0.1" '; cat "$stage/coordinator.pub") > "$stage/authorized_keys"
sudo install -m 0644 "$stage/authorized_keys" "$auth/authorized_keys"
printf '[127.0.0.1]:%s %s\n' "$port" "$(cut -d' ' -f1-2 "$stage/host.pub")" > "$stage/known_hosts"
printf '[127.0.0.1]:%s %s\n' "$port" "$(cut -d' ' -f1-2 "$stage/wrong-host.pub")" > "$stage/wrong_known_hosts"
cat > "$stage/relay" <<EOF
#!/bin/sh
test -z "\${SSH_AUTH_SOCK:-}" || exit 2
export PYTHONPATH=$stage/runtime
exec /usr/bin/python3 -B -m community_grid.physical $stage/worker/state.db --coordinator A --node B
EOF
sudo chown root:root "$stage/relay"
sudo chmod 755 "$stage/relay"
cat > "$stage/sshd_config" <<EOF
Port $port
ListenAddress 127.0.0.1
HostKey $stage/host
PidFile $stage/sshd.pid
AuthorizedKeysFile $auth/authorized_keys
StrictModes yes
AllowUsers $account@127.0.0.1
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
DisableForwarding yes
AllowAgentForwarding no
AllowTcpForwarding no
X11Forwarding no
PermitTTY no
PermitUserRC no
PermitUserEnvironment no
UsePAM yes
LoginGraceTime 5
MaxSessions 1
ForceCommand $stage/relay
EOF
sudo /usr/sbin/sshd -t -f "$stage/sshd_config"
sudo /usr/sbin/sshd -T -f "$stage/sshd_config" -C user="$account",host=localhost,addr=127.0.0.1 \
    | grep -E '^(listenaddress|allowusers|authenticationmethods|forcecommand|disableforwarding|permittty|permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowagentforwarding|allowtcpforwarding|x11forwarding) ' \
    | tee "$evidence/sshd-effective.txt"
sudo /usr/sbin/sshd -f "$stage/sshd_config" -E "$stage/sshd.log"
opts=(-F /dev/null -p "$port" -o ConnectTimeout=2 -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null
      -o HostKeyAlgorithms=ssh-ed25519 -o BatchMode=yes -o IdentitiesOnly=yes)
target="$account@127.0.0.1"
approved=(ssh "${opts[@]}" -o "UserKnownHostsFile=$stage/known_hosts" -i "$stage/coordinator")
probe='{"version":2,"action":"probe"}'
deny() {
    local name=$1 status
    shift
    set +e
    printf '%s' "$probe" | timeout 5 "$@" > "$stage/denial.log" 2>&1
    status=$?
    set -e
    if test "$status" -eq 0 || test "$status" -eq 124; then
        cat "$stage/denial.log"
        echo "$name: unexpected exit $status"; exit 1
    fi
    printf '%s: rejected (exit %s)\n' "$name" "$status"
    cat "$stage/denial.log"
}
{
    printf '%s' "$probe" | "${approved[@]}" -T "$target" | tee "$stage/probe.json"
    deny unknown-key ssh "${opts[@]}" -o "UserKnownHostsFile=$stage/known_hosts" -i "$stage/unknown" -T "$target"
    grep -F 'Permission denied (publickey)' "$stage/denial.log"
    deny wrong-host-pin ssh "${opts[@]}" -o "UserKnownHostsFile=$stage/wrong_known_hosts" -i "$stage/coordinator" -T "$target"
    grep -E 'Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED' "$stage/denial.log"
    deny shell-command "${approved[@]}" -T "$target" true
    deny pty "${approved[@]}" -tt "$target"
    grep -F 'PTY allocation request failed' "$stage/denial.log"
    deny remote-forwarding "${approved[@]}" -o ExitOnForwardFailure=yes -N -R 0:127.0.0.1:22 "$target"
    grep -F 'remote port forwarding failed' "$stage/denial.log"
    deny subsystem "${approved[@]}" -s "$target" sftp
    eval "$(ssh-agent -s)" >/dev/null
    ssh-add "$stage/coordinator" 2>/dev/null
    printf '%s' "$probe" | "${approved[@]}" -A -T "$target" | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"]==2'
    echo 'Agent forwarding absent in forced relay when explicitly requested'
} 2>&1 | tee "$evidence/ssh-negative-tests.txt"
sudo -u "$account" sh -c 'echo "$$" > "$1/worker/pid"; export PYTHONPATH="$1/runtime"; exec /usr/bin/python3 -B -m community_grid.worker_v2 "$1/worker/state.db" --coordinator A --node B' sh "$stage" &
PYTHONPATH=runtime python3 -B - "$stage" "$account" "$port" <<'PY' | tee "$evidence/real-ssh-graph.txt"
import hashlib,json,pathlib,subprocess,sys,time
from community_grid.enrollment import Registry
from community_grid.native import Runtime

stage=pathlib.Path(sys.argv[1])
admin=stage/'admin'
admin.mkdir()
manifest=admin/'nodes.json'
host=' '.join((stage/'host.pub').read_text().split()[:2])
fingerprint=subprocess.check_output(['ssh-keygen','-lf',str(stage/'host.pub')],text=True).split()[1]
entry=dict(node='B',address='127.0.0.1',port=int(sys.argv[3]),account=sys.argv[2],host_key=host,
           fingerprint=fingerprint,identity_file=str(stage/'coordinator'),protocols=[2],
           operations={'text.normalize':1},cpu=1,memory_mib=128,revoked=False)
manifest.write_text(json.dumps(dict(version=1,workers=[entry])))
manifest.chmod(0o600)
registry=Registry(manifest,admin)
try:
    runtime=Runtime(admin,registry,boot_id='disposable-linux-github-runner')
    graph=dict(id='ssh',owner='local',version=1,timeout=25,units={'normalize':dict(
        op='text.normalize',input_type='text',output_type='text',nodes=['B'],depends=[],
        value='  seed   library ',cpu=1,ram_mib=128,timeout=15,attempts=2,side_effects='none')})
    runtime.request({'action':'submit','graph':graph})
    end=time.monotonic()+25
    while time.monotonic()<end:
        runtime.tick()
        s=runtime.request(dict(action='graph-status',id='ssh'))
        if s['normalize']['status']=='completed': break
        time.sleep(.08)
    else: raise AssertionError(s)
    result=runtime.request(dict(action='result',id='ssh',unit='normalize'))
    assert result['value']=='seed library'
    assert result['artifact']['sha256']==hashlib.sha256(b'"seed library"').hexdigest()
    print(json.dumps({'authenticated_capabilities':runtime.request({'action':'nodes'}),
                      'status':s,'verified_result':result},sort_keys=True))
finally:
    registry.close()
PY
