"""Validate the explicitly approved direct-link addresses before live SSH starts."""
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys


def validate_addresses(dell, lenovo, assigned, route):
    server = ipaddress.IPv4Address(dell)
    client = ipaddress.IPv4Address(lenovo)
    static = dell == "192.168.50.2" and lenovo == "192.168.50.1"
    ics = lenovo == "192.168.137.1" and server in ipaddress.IPv4Network("192.168.137.0/24")
    if not (static or ics) or server.packed[-1] in (0, 1, 255):
        raise ValueError("Expected the static direct pair or an ICS host and its Lenovo gateway")
    if f"{server}/24" not in assigned or route.get("gateway"):
        raise ValueError("The Dell address and on-link Lenovo route must be on the selected interface")
    if ics and not any(
        item.get("dst") == "default"
        and item.get("gateway") == lenovo
        and item.get("protocol") == "dhcp"
        for item in route.get("defaults", [])
    ):
        raise ValueError("The selected interface needs a DHCP default route via the Lenovo")


def main(interface, dell, lenovo):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface):
        raise ValueError("Invalid interface name")
    sysfs = Path("/sys/class/net") / interface
    if not (sysfs / "device").exists() or (sysfs / "type").read_text().strip() != "1":
        raise ValueError("Select the physical wired Ethernet interface")
    if (sysfs / "wireless").exists() or (sysfs / "phy80211").exists():
        raise ValueError("Select a wired interface")
    addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr", "show", "dev", interface]))
    assigned = {f"{item['local']}/{item['prefixlen']}" for item in addresses[0]["addr_info"]}
    ipaddress.IPv4Address(dell)
    ipaddress.IPv4Address(lenovo)
    route = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "get", lenovo, "from", dell]))[0]
    if route.get("dev") != interface:
        raise ValueError("The route to Lenovo does not use the selected Ethernet interface")
    route["defaults"] = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "route", "show", "default", "dev", interface]
    ))
    validate_addresses(dell, lenovo, assigned, route)


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
