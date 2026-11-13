"""Cross-platform, dependency-free hardware capability discovery."""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def _mem_total_bytes():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _gpu_devices():
    devices = []
    command = shutil.which("vulkaninfo")
    if command:
        try:
            result = subprocess.run([command, "--summary"], capture_output=True, text=True, timeout=8, check=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "deviceName" in line and "=" in line:
                        name = line.split("=", 1)[1].strip()
                        if name and name not in devices:
                            devices.append(name)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return [{"name": name, "backend": "vulkan", "verified_for_llm": False} for name in devices]


def discover():
    return {
        "schema_version": 1,
        "hostname": platform.node(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "memory_total_bytes": _mem_total_bytes(),
        "accelerators": _gpu_devices(),
    }


def main():
    print(json.dumps(discover(), indent=2))


if __name__ == "__main__":
    main()
