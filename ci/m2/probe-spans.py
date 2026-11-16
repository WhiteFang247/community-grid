"""Separate client-side timing control under the packaged service's resource limits."""
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time
from unittest.mock import patch
import uuid

from community_grid import physical
from community_grid.enrollment import Peer, read_manifest


def trace(name, entry):
    endpoint = f'[{entry["address"]}]:{entry["port"]}'
    with tempfile.TemporaryDirectory() as path:
        pin = Path(path) / "pin"
        pin.write_text(f'{endpoint} {entry["host_key"]}\n')
        with patch.object(physical, "process_transport", side_effect=lambda argv, *, timeout: (argv, timeout)):
            argv, limit = physical.ssh_transport(entry["address"], entry["account"], entry["identity_file"],
                                                 pin, entry["port"], enrolled=True)
        assert limit == 3
        phases = {}
        stdout = bytearray()
        with tempfile.TemporaryFile() as source:
            source.write(b'{"version":2,"action":"probe"}')
            source.seek(0)
            start = time.monotonic()
            with subprocess.Popen([argv[0], "-vv", *argv[1:]], stdin=source, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as child:
                phases["spawn_return"] = time.monotonic()-start

                def drain_debug():
                    markers = {b"Connection established": "connected", b"Server host key:": "host_key",
                               b"SSH2_MSG_NEWKEYS received": "key_exchange", b"Authenticated to ": "authenticated",
                               b"Entering interactive session": "session_requested"}
                    for raw in child.stderr:
                        for marker, label in markers.items():
                            if marker in raw:
                                phases.setdefault(label, time.monotonic()-start)

                def drain_response():
                    first = child.stdout.read(1)
                    if first:
                        phases["first_response_byte"] = time.monotonic()-start
                        stdout.extend(first + child.stdout.read(physical.LIMIT))
                        phases["response_read"] = time.monotonic()-start

                readers = [Thread(target=target) for target in (drain_debug, drain_response)]
                for reader in readers:
                    reader.start()
                outcome = "exit"
                try:
                    child.wait(timeout=max(0, start + limit - time.monotonic()))
                except subprocess.TimeoutExpired:
                    outcome = "deadline"
                    child.kill()
                    child.wait()
                phases["reaped"] = time.monotonic()-start
                for reader in readers:
                    reader.join()
                code = child.returncode
        boot_id = None
        if code == 0 and outcome == "exit":
            try:
                response = json.loads(stdout)
                if response.get("version") != 2:
                    raise ValueError("protocol")
                peer = Peer(entry, None, None)
                peer._probe(response)
                boot_id = peer.boot_id
                phases["validated"] = time.monotonic()-start
                outcome = "valid"
            except (ValueError, TypeError, KeyError):
                outcome = "invalid-response"
        print("M2_SPAN " + json.dumps({"id": str(uuid.uuid4()), "node": name,
                                      "start": start, "outcome": outcome,
                                      "exit_code": code, "boot_id": boot_id, "seconds": phases}), flush=True)


if __name__ == "__main__":
    nodes = read_manifest(Path("/etc/cgos/distributed/nodes.json"))
    for order in ("AB", "BA", "AB"):
        for node in order:
            trace(node, nodes[node])
