#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
evidence="$PWD/evidence"
mkdir -p "$evidence"
stage=$(mktemp -d)
auth=$(sudo mktemp -d /run/cgos-ci-auth.XXXXXXXX)
sudo chmod 755 "$auth"
port=22227
port_a=22228
account=cgos-ci-relay
chmod 755 "$stage"
cleanup() {
    if test -s "$stage/sshd.pid"; then sudo kill "$(cat "$stage/sshd.pid")" || true; fi
    if sudo test -s "$stage/worker/pid"; then sudo kill "$(sudo cat "$stage/worker/pid")" || true; fi
    if test -s "$stage/sshd-a.pid"; then sudo kill "$(cat "$stage/sshd-a.pid")" || true; fi
    if sudo test -s "$stage/worker-a/pid"; then sudo kill "$(sudo cat "$stage/worker-a/pid")" || true; fi
    if test -s "$stage/sshd.log"; then sudo cat "$stage/sshd.log" > "$evidence/sshd.log"; fi
    if test -n "${SSH_AGENT_PID:-}"; then ssh-agent -k >/dev/null; fi
    if test -s "$stage/sshd-a.log"; then sudo cat "$stage/sshd-a.log" > "$evidence/sshd-a.log"; fi
    sudo rm -rf -- "$stage" "$auth"
}
trap cleanup EXIT
sudo useradd -M -d "$stage/worker" -s /bin/sh "$account"
sudo install -d -m 0755 /run/sshd
mkdir "$stage/runtime"
cp -r runtime/community_grid "$stage/runtime/"
sudo install -d -m 0700 -o "$account" "$stage/worker"
for name in host host-a coordinator unknown wrong-host; do
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
    trap 'ssh-agent -k >/dev/null' EXIT
    ssh-add "$stage/coordinator" 2>/dev/null
    printf '%s' "$probe" | "${approved[@]}" -A -T "$target" | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"]==2'
    echo 'Agent forwarding absent in forced relay when explicitly requested'
} 2>&1 | tee "$evidence/ssh-negative-tests.txt"
sudo -u "$account" sh -c 'echo "$$" > "$1/worker/pid"; export PYTHONPATH="$1/runtime"; exec /usr/bin/python3 -B -m community_grid.worker_v2 "$1/worker/state.db" --coordinator A --node B' sh "$stage" &
sudo install -d -m 0700 -o "$account" "$stage/worker-a"
sed -e 's@/worker/state.db@/worker-a/state.db@' -e 's/--node B/--node A/' "$stage/relay" > "$stage/relay-a"
sudo chown root:root "$stage/relay-a"
sudo chmod 755 "$stage/relay-a"
sed -e "s/^Port .*/Port $port_a/" -e 's@/host$@/host-a@' -e 's@/sshd.pid$@/sshd-a.pid@' \
    -e 's@/relay$@/relay-a@' "$stage/sshd_config" > "$stage/sshd-a_config"
sudo /usr/sbin/sshd -t -f "$stage/sshd-a_config"
sudo /usr/sbin/sshd -f "$stage/sshd-a_config" -E "$stage/sshd-a.log"
sudo -u "$account" sh -c 'echo "$$" > "$1/worker-a/pid"; export PYTHONPATH="$1/runtime"; exec /usr/bin/python3 -B -m community_grid.worker_v2 "$1/worker-a/state.db" --coordinator A --node A' sh "$stage" &
PYTHONPATH=runtime python3 -B - "$stage" "$account" "$port" "$port_a" <<'PYTHON' | tee "$evidence/real-ssh-graph.txt"
import hashlib,json,pathlib,subprocess,sys,time
from community_grid.enrollment import Registry
from community_grid.native import Runtime

stage=pathlib.Path(sys.argv[1])
admin=stage/'admin'
admin.mkdir()
manifest=admin/'nodes.json'
entries=[]
for node, port, name in [('A',sys.argv[4],'host-a.pub'),('B',sys.argv[3],'host.pub')]:
    host=' '.join((stage/name).read_text().split()[:2])
    fingerprint=subprocess.check_output(['ssh-keygen','-lf',str(stage/name)],text=True).split()[1]
    entries.append(dict(node=node,address='127.0.0.1',port=int(port),account=sys.argv[2],host_key=host,
                        fingerprint=fingerprint,identity_file=str(stage/'coordinator'),protocols=[2],
                        operations={'text.normalize':1,'text.sha256':1},cpu=1,memory_mib=128,revoked=False))
assert entries[0]['fingerprint'] != entries[1]['fingerprint']
manifest.write_text(json.dumps(dict(version=1,workers=entries)))
manifest.chmod(0o600)
registry=Registry(manifest,admin)
try:
    runtime=Runtime(admin,registry,boot_id='disposable-linux-github-runner')
    graph=json.loads(pathlib.Path('distro/m2/reference-graph.json').read_text())
    runtime.request({'action':'submit','graph':graph})
    end=time.monotonic()+55
    while time.monotonic()<end:
        runtime.tick()
        states=runtime.request(dict(action='graph-status',id=graph['id']))
        if states['final']['status']=='completed':
            break
        time.sleep(.08)
    else:
        raise AssertionError(states)
    expected={'normalize':'seed library'}
    expected['hash']=hashlib.sha256(expected['normalize'].encode()).hexdigest()
    expected['final']=hashlib.sha256(expected['hash'].encode()).hexdigest()
    result={}
    for task, node in [('normalize','A'),('hash','B'),('final','A')]:
        assert states[task]['status']=='completed' and states[task]['node']==node
        reply=runtime.request(dict(action='result',id=graph['id'],unit=task))
        assert reply['value']==expected[task]
        assert reply['artifact']['sha256']==hashlib.sha256(json.dumps(expected[task]).encode()).hexdigest()
        result[task]=reply
    capabilities=runtime.request({'action':'nodes'})
    assert set(capabilities)=={'A','B'} and all(peer['online'] for peer in capabilities.values())
    print(json.dumps({'authenticated_capabilities':capabilities,'status':states,
                      'verified_results':result},sort_keys=True))
finally:
    registry.close()
PYTHON
