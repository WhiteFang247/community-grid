"""Boot a verified CGOS ISO in two isolated QEMU guests on a disposable Linux runner."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

SIZE = 1536
ROOT = Path(__file__).resolve().parent
EVIDENCE = Path("evidence").resolve()
ISO = Path("iso/live-image-amd64.hybrid.iso").resolve()


def qemu_accel():
    try:
        with open("/dev/kvm", "r+b", buffering=0) as f:
            if fcntl.ioctl(f, 0xAE00) == 12:
                return "kvm"
    except OSError:
        pass
    return "tcg"


def key(path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    public = Path(str(path) + ".pub")
    fingerprint = subprocess.check_output(["ssh-keygen", "-lf", str(public)], text=True).split()[1]
    return " ".join(public.read_text().split()[:2]), fingerprint


def make_media(directory, diagnostic=False, acceptance=False):
    client = directory / "client"
    public, _ = key(client)
    key(directory / "unknown")
    bad, _ = key(directory / "wrong-host")
    entries, disks = [], {}
    for index, role in enumerate("AB", 1):
        host, fingerprint = key(directory / f"host-{role}")
        entries.append(dict(node=role, address=f"10.44.0.{index}", port=22227,
                            account="cgos-worker", host_key=host, fingerprint=fingerprint,
                            identity_file="/etc/cgos/distributed/client", protocols=[2],
                            operations={"text.normalize": 1, "text.sha256": 1},
                            cpu=1, memory_mib=128, revoked=False))
    for role in "AB":
        folder = directory / role
        folder.mkdir(mode=0o700)
        (folder / "role").write_text(role)
        (folder / "client.pub").write_text(public + "\n")
        shutil.copyfile(directory / f"host-{role}", folder / "host")
        shutil.copyfile(ROOT / "setup-guest.sh", folder / "setup.sh")
        shutil.copyfile(ROOT / "audit-worker.py", folder / "audit.py")
        if role == "A":
            shutil.copyfile(client, folder / "client")
            shutil.copyfile(directory / "unknown", folder / "unknown")
            shutil.copyfile(ROOT / "check-ssh.sh", folder / "auth.sh")
            shutil.copyfile(ROOT / "trials.py", folder / "trials.py")
            if diagnostic:
                shutil.copyfile(ROOT / "diagnose-latency.py", folder / "diagnose.py")
            if acceptance:
                shutil.copyfile(ROOT / "acceptance-observer.py", folder / "observe.py")
            (folder / "nodes.json").write_text(json.dumps(dict(version=1, workers=entries)))
            for name, field in (("known_hosts", None), ("wrong_hosts", bad)):
                (folder / name).write_text("".join(
                    f"[{entry['address']}]:22227 {field or entry['host_key']}\n" for entry in entries))
        path = directory / f"tools-{role}.iso"
        subprocess.run(["xorriso", "-as", "mkisofs", "-r", "-V", "CGOSSET", "-o", str(path), str(folder)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        disks[role] = path
    return disks


class Guest:
    def __init__(self, directory, role, disk, accel, port):
        self.directory, self.role = directory, role
        self.base = EVIDENCE / role
        self.base.mkdir()
        self.start = time.monotonic()
        self.serial = self.base / "kernel-serial.log"
        self.channel_path = directory / f"s{role}"
        self.qmp_path = directory / f"q{role}"
        self.counter = 0
        self.output = bytearray()
        self.stack = ExitStack()
        cmd = ["qemu-system-x86_64", "-machine", "q35,accel=" + accel, "-smp", "2",
               "-m", str(SIZE), "-display", "none", "-boot", "order=d,strict=on", "-no-reboot",
               "-cdrom", str(ISO), "-drive", f"if=virtio,file={disk},format=raw,readonly=on",
               "-serial", f"file:{self.serial}", "-serial", f"unix:{self.channel_path},server=on,wait=off",
               "-qmp", f"unix:{self.qmp_path},server=on,wait=off", "-monitor", "none"]
        endpoint = ("listen" if role == "A" else "connect") + f"=127.0.0.1:{port}"
        cmd += ["-netdev", "socket,id=private," + endpoint,
                "-device", f"e1000,netdev=private,id=nic,mac=52:54:00:44:00:0{1 if role=='A' else 2}"]
        self.mode = "bios" if role == "A" else "uefi"
        if role == "B":
            varsfile = directory / "ovmf-vars.fd"
            shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", varsfile)
            cmd += ["-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
                    "-drive", f"if=pflash,format=raw,file={varsfile}"]
        else:
            cmd += ["-bios", "/usr/share/seabios/bios.bin"]
        (self.base / "vm.json").write_text(json.dumps(dict(
            role=role, accelerator=accel, firmware=self.mode, ram_mib=SIZE, vcpus=2,
            qemu=subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).splitlines()[0]
        ), indent=2))
        self.log = self.stack.enter_context((self.base / "qemu.log").open("wb"))
        self.console = self.stack.enter_context((self.base / "command-serial.log").open("wb", buffering=0))
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            monitor = self.stack.enter_context(self.connect(self.qmp_path))
            self.control = self.stack.enter_context(monitor.makefile("rwb"))
            self.control.readline()
            self.qmp("qmp_capabilities")
            self.channel = self.stack.enter_context(self.connect(self.channel_path))
            self.channel.settimeout(.4)
        except BaseException:
            self.close()
            raise

    def connect(self, path):
        end = time.monotonic() + 15
        sock = socket.socket(socket.AF_UNIX)
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                sock.connect(str(path))
                sock.settimeout(10)
                return sock
            except OSError:
                time.sleep(.1)
        sock.close()
        raise RuntimeError(f"{self.role} QEMU channel not ready")

    def qmp(self, name, args=None):
        self.control.write(json.dumps(dict(execute=name, arguments=args or {})).encode() + b"\n")
        self.control.flush()
        while True:
            line = self.control.readline()
            if not line:
                raise RuntimeError("QMP closed")
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(response)
            if "return" in response:
                return response["return"]

    def shot(self, name):
        self.qmp("screendump", {"filename": str(self.base / (name + ".ppm"))})

    def keys(self, *keys):
        self.qmp("send-key", {"keys": [{"type": "qcode", "data": k} for k in keys], "hold-time": 30})
        time.sleep(.055)

    def type(self, text):
        symbols = {" ": "spc", "/": "slash", "-": "minus", "_": ("shift", "minus"),
                   "+": ("shift", "equal"), "=": "equal", ".": "dot", ",": "comma"}
        for character in text:
            keycode = symbols.get(character, ("shift", character.lower()) if character.isupper() else character)
            self.keys(*(keycode if isinstance(keycode, tuple) else (keycode,)))

    def receive(self):
        try:
            data = self.channel.recv(65536)
        except TimeoutError:
            return
        if not data:
            raise RuntimeError(f"{self.role} serial channel closed")
        self.console.write(data)
        self.output.extend(data)
        if len(self.output) > 32 * 1024 * 1024:
            raise RuntimeError("serial capture exceeded budget")

    def run(self, command, seconds=90, event=None):
        self.counter += 1
        marker = f"CGOS_DONE_{self.counter:03d}"
        start = len(self.output)
        self.channel.sendall((command + f"; printf 'CGOS_DONE_%03d=%s\\n' {self.counter} \"$?\"\r").encode())
        end = time.monotonic() + seconds
        seen = set()
        while time.monotonic() < end and self.proc.poll() is None:
            self.receive()
            result = bytes(self.output[start:])
            if event is not None:
                for match in re.finditer(rb"^M2_LINK_(DOWN|UP)_(\d+)\r*$", result, re.M):
                    action, number = match.groups()
                    if (action, number) not in seen:
                        event(action)
                        seen.add((action, number))
            match = re.search(marker.encode() + rb"=(\d+)\r*\n", result)
            if match:
                if match[1] != b"0":
                    raise RuntimeError(f"{self.role} serial command {self.counter} failed with {match[1].decode()}")
                return result.decode(errors="replace").replace("\r", "")
        raise RuntimeError(f"{self.role} serial command {self.counter} timed out")

    def boot(self, kernel):
        time.sleep(22 if self.mode == "uefi" else 15)
        self.shot("stock-menu")
        args = " console=ttyS0,115200n8 systemd.debug_shell=ttyS1"
        if self.mode == "bios":
            self.keys("tab")
            time.sleep(1)
            self.keys("end")
            self.type(args)
            self.keys("ret")
        else:
            self.keys("c")
            time.sleep(1)
            self.type(f"linux /live/vmlinuz-{kernel} boot=live components username=user hostname=cgos findiso=" + args)
            self.keys("ret")
            time.sleep(2)
            self.type(f"initrd /live/initrd.img-{kernel}")
            self.keys("ret")
            time.sleep(2)
            self.type("boot")
            self.keys("ret")
        end = self.start + 240
        wire = b"printf 'CGOS_SERIAL_%s\\n' READY; id -u; tty\r"
        next_probe = 0.0
        while time.monotonic() < end and self.proc.poll() is None:
            log = self.serial.read_text(errors="replace") if self.serial.exists() else ""
            if "systemd[1]" in log and time.monotonic() >= next_probe:
                self.channel.sendall(wire)
                next_probe = time.monotonic() + 5
            self.receive()
            if not re.search(rb"CGOS_SERIAL_READY\r*\n0\r*\n/dev/ttyS1\r*\n", self.output):
                continue
            if "Linux version " not in log:
                raise RuntimeError("Linux kernel serial evidence missing")
            elapsed = time.monotonic() - self.start
            info = self.run("cat /proc/sys/kernel/random/boot_id", max(1, end - time.monotonic()))
            clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", info)
            identity, = re.findall(r"(?<![0-9a-f-])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f-])", clean)
            (self.base / "boot.json").write_text(json.dumps(dict(boot_id=identity, serial_ready_s=elapsed)))
            report = self.run("install -d -m 0700 /run/cgos-test-private /run/cgos-test-private/media && mount -o ro /dev/vda /run/cgos-test-private/media && /bin/sh /run/cgos-test-private/media/setup.sh", 100)
            if f"FIRMWARE={self.mode}" not in report or f"M2_GUEST_READY_{self.role}" not in report:
                raise RuntimeError("Firmware or service verification failed")
            return identity
        raise RuntimeError(f"{self.role} failed to reach serial command shell within 240 seconds")

    def close(self):
        if self.proc.poll() is None:
            if hasattr(self, "control"):
                try:
                    state = self.qmp("query-status")
                    (self.base / "qmp-status.json").write_text(json.dumps(state))
                    self.shot("final-screen")
                except (OSError, RuntimeError):
                    pass
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.stack.close()


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--diagnostic", action="store_true")
    modes.add_argument("--acceptance", action="store_true")
    options = parser.parse_args()
    diagnostic, acceptance = options.diagnostic, options.acceptance
    EVIDENCE.mkdir(exist_ok=True)
    kernel, = [p.name.removeprefix("config-") for p in Path("iso/manifest").glob("config-*")]
    accel = qemu_accel()
    with tempfile.TemporaryDirectory(prefix="cgos-m2-") as path, ExitStack() as stack:
        directory = Path(path)
        disks = make_media(directory, diagnostic, acceptance)
        with closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        a = Guest(directory, "A", disks["A"], accel, port)
        stack.callback(a.close)
        b = Guest(directory, "B", disks["B"], accel, port)
        stack.callback(b.close)
        try:
            with ThreadPoolExecutor(2) as pool:
                ids = [f.result() for f in (pool.submit(a.boot, kernel), pool.submit(b.boot, kernel))]
            if ids[0] == ids[1]:
                raise RuntimeError("Guest boot IDs are not independent")
            a.run("/bin/bash /run/cgos-test-private/media/auth.sh", 125)
            if acceptance:
                a.run("install -m 0644 /run/cgos-test-private/media/observe.py /run/cgos-acceptance-observe.py && "
                      "install -d /run/systemd/system/cgos-coordinator.service.d && "
                      "printf '%s\\n' '[Service]' 'ExecStart=' "
                      "'ExecStart=/usr/bin/python3 /run/cgos-acceptance-observe.py --socket /run/cgos-coordinator/api.sock serve --state /var/lib/cgos-coordinator --enrollment /etc/cgos/distributed/nodes.json' "
                      ">/run/systemd/system/cgos-coordinator.service.d/acceptance.conf && "
                      "systemctl daemon-reload && systemctl restart cgos-coordinator.service && "
                      "timeout 25s sh -c 'until test -S /run/cgos-coordinator/api.sock; do sleep .2; done'", 45)
            if diagnostic:
                a.run("systemctl stop cgos-coordinator.service && "
                      "PYTHONPATH=/opt/cgos/runtime timeout 70s python3 -Bu /run/cgos-test-private/media/diagnose.py", 80)
            else:
                a.run("PYTHONPATH=/opt/cgos/runtime python3 -B /run/cgos-test-private/media/trials.py", 10500 if acceptance else 1250,
                      lambda action: b.qmp("set_link", {"name": "nic", "up": action == b"UP"}))
                for guest in (a, b):
                    guest.run("python3 -B /run/cgos-test-private/media/audit.py && "
                              "systemctl show cgos-worker.service -p MemoryPeak -p CPUUsageNSec", 45)
        finally:
            for guest in (a,b):
                if acceptance and guest is a:
                    try:
                        guest.run("cat /var/lib/cgos-coordinator/acceptance-rpc.jsonl", 90)
                    except (OSError, RuntimeError):
                        pass
                if diagnostic or acceptance:
                    try:
                        guest.run("python3 -B /run/cgos-test-private/media/audit.py; "
                                  "systemctl show cgos-worker.service cgos-worker-relay.service "
                                  "-p Id -p MemoryPeak -p CPUUsageNSec; cat /proc/loadavg; "
                                  "grep -E '^(cpu |MemAvailable:)' /proc/stat /proc/meminfo", 35)
                    except (OSError, RuntimeError):
                        pass
                try:
                    guest.run("journalctl -u cgos -u cgos-coordinator -u cgos-worker -u cgos-worker-relay --no-pager -n 150", 30)
                except (OSError,RuntimeError):
                    pass


if __name__ == "__main__":
    main()
