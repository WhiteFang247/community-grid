#!/bin/sh
# Run only after reviewing the paths and approving privileged deployment.
set -eu

source_dir=${1:?pass the new Community Grid source directory}
assets_dir=${2:?pass the verified inference asset directory}
public_key=${3:?pass the Lenovo Ed25519 public key file}
test "$(id -u)" -eq 0
test -d "$source_dir/runtime/community_grid"
test -f "$source_dir/distro/inference/cgos-inference.service"
test -f "$assets_dir/llama-server"
test -f "$assets_dir/qwen2.5-1.5b-instruct-q4_k_m.gguf"
test -f "$public_key"
test ! -e /opt/cgos
test ! -e /etc/cgos/inference
test ! -e /var/lib/cgos-inference
! id cgos-infer >/dev/null 2>&1

key_fingerprint=$(ssh-keygen -lf "$public_key" -E sha256 | awk '{print $2}')
test "$key_fingerprint" = 'SHA256:4P6uSUrMa4e1kOQLwwS2p+gKEIPfIPZO8ToomS+DPzM'
key_body=$(awk 'NR==1 && $1=="ssh-ed25519" && $2 ~ /^[A-Za-z0-9+/=]+$/ {print $1 " " $2}' "$public_key")
test -n "$key_body"
test "$(wc -l < "$public_key")" -eq 1

model_hash=$(sha256sum "$assets_dir/qwen2.5-1.5b-instruct-q4_k_m.gguf" | awk '{print $1}')
backend_hash=$(sha256sum "$assets_dir/llama-server" | awk '{print $1}')
test "$model_hash" = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'
test "$backend_hash" = '4dbed0b2d945dd9752f8945942209e0eede6d4ad46c067a81f7051d0c3796963'
LD_LIBRARY_PATH="$assets_dir" ldd "$assets_dir/llama-server" | grep -q 'libllama.so'
! LD_LIBRARY_PATH="$assets_dir" ldd "$assets_dir/llama-server" | grep -q 'not found'
/usr/sbin/sshd -t

# Git is the only missing Debian package observed on the clean installation.
if ! command -v git >/dev/null 2>&1; then
    apt-get update
    apt-get install --no-install-recommends -y git
fi

useradd --system --home-dir /var/lib/cgos-inference --shell /bin/sh --no-create-home cgos-infer
install -d -o root -g root -m 0755 /opt/cgos /opt/cgos/runtime /opt/cgos/inference
cp -a "$source_dir/runtime/." /opt/cgos/runtime/
chown -R root:root /opt/cgos/runtime
chmod -R go-w /opt/cgos/runtime
install -o root -g root -m 0755 "$assets_dir/llama-server" /opt/cgos/inference/llama-server
install -o root -g root -m 0644 "$assets_dir/qwen2.5-1.5b-instruct-q4_k_m.gguf" /opt/cgos/inference/qwen2.5-1.5b-instruct-q4_k_m.gguf
for library in "$assets_dir"/*.so; do
    install -o root -g root -m 0755 "$library" /opt/cgos/inference/
done
install -d -o root -g root -m 0755 /etc/cgos /etc/cgos/inference
install -o root -g root -m 0600 "$source_dir/distro/inference/worker.env.example" /etc/cgos/inference/worker.env
install -d -o cgos-infer -g cgos-infer -m 0700 /var/lib/cgos-inference /var/lib/cgos-inference/.ssh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' |
    install -o cgos-infer -g cgos-infer -m 0600 /dev/stdin /var/lib/cgos-inference/operator-token
printf 'restrict,from="192.168.137.1",command="/usr/local/libexec/cgos-inference-relay" %s\n' "$key_body" |
    install -o cgos-infer -g cgos-infer -m 0600 /dev/stdin /var/lib/cgos-inference/.ssh/authorized_keys
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$source_dir/distro/inference/cgos-inference-relay" /usr/local/libexec/cgos-inference-relay
install -o root -g root -m 0644 "$source_dir/distro/inference/cgos-inference.service" /etc/systemd/system/cgos-inference.service
systemctl daemon-reload
systemctl enable --now cgos-inference.service
systemctl is-active --quiet cgos-inference.service
echo 'Community Grid inference service active; verify through pinned SSH before submitting a job.'
