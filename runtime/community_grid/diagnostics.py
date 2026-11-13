"""Safe, read-only host diagnostics for first boot and Codex troubleshooting."""
from __future__ import annotations
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .hardware import discover

COMMANDS = {
    "network_addresses": ["ip", "-brief", "address"],
    "network_routes": ["ip", "route"],
    "pci_devices": ["lspci", "-nn"],
    "usb_devices": ["lsusb"],
    "block_devices": ["lsblk", "-J", "-o", "NAME,TYPE,SIZE,MODEL,TRAN,MOUNTPOINTS"],
    "service_status": ["systemctl", "is-active", "community-grid.service"],
}
MAX_OUTPUT = 16_384

def _run(argv):
    if not shutil.which(argv[0]):
        return {"available": False, "output": "", "exit_code": None}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False)
        output = result.stdout[:MAX_OUTPUT]
        if result.returncode and result.stderr:
            output += "\n" + result.stderr[:2048]
        return {"available": True, "output": output, "exit_code": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "error": type(exc).__name__, "output": "", "exit_code": None}

def collect():
    """Does not read credentials, environment variables, logs or user files."""
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kernel": platform.release(),
        "hardware": discover(),
        "commands": {key: _run(command) for key, command in COMMANDS.items()},
        "notice": "May contain hostnames, MAC addresses, local IP addresses and storage metadata; review before sharing.",
    }

def main():
    print(json.dumps(collect(), indent=2))

if __name__ == "__main__":
    main()
