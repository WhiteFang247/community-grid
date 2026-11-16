"""Add measurement around the ISO's native coordinator transport only."""
import json
import os
import time

from community_grid import enrollment, native
from community_grid.physical import ssh_transport


def observed_transport(*args):
    wire = ssh_transport(*args, enrolled=True)

    def exchange(request):
        start = time.monotonic()
        lease = request.get("lease", {})
        row = {"node_address": args[0], "action": request["action"], "started": start,
               **{key: lease[key] for key in ("graph", "task", "attempt", "generation") if key in lease}}
        try:
            response = wire(request)
            row["status"] = response.get("status")
            return response
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["seconds"] = time.monotonic() - start
            fd = os.open("/var/lib/cgos-coordinator/acceptance-rpc.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, ("M2_RPC " + json.dumps(row, separators=(",", ":")) + "\n").encode())
            finally:
                os.close(fd)
    return exchange


class ObservedRegistry(enrollment.Registry):
    def __init__(self, path, state):
        super().__init__(path, state, transport_factory=observed_transport)


if __name__ == "__main__":
    enrollment.Registry = ObservedRegistry
    native.main()
