"""One authenticated worker API request over an already pinned SSH session."""
import http.client
import json
import os
import re
import sys
import stat

JOB = r"/api/jobs/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


def request(port, token, method, path, body=None):
    if not isinstance(path, str) or not (
        method == "GET" and body is None and (path == "/api/worker" or re.fullmatch(JOB, path))
        or method == "POST" and (
            path == "/api/jobs" and isinstance(body, dict) and set(body) == {"id", "prompt"}
            or body is None and re.fullmatch(JOB + "/cancel", path)
        )
    ):
        raise ValueError("Unsupported worker operation")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, {
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
        })
        with conn.getresponse() as reply:
            return reply.status, json.loads(reply.read(262145))
    finally:
        conn.close()


def main():
    # Fixed command and operator-defined token file. Requests travel on stdin.
    from pathlib import Path
    port = int(sys.argv[1])
    with Path(sys.argv[2]).open() as secret:
        info = os.fstat(secret.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("Expected a private mode-0600 token file")
        token = secret.read(129).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Invalid operator token")
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192:
        raise ValueError("Request too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"method", "path", "body"}:
        raise ValueError("Invalid relay request")
    print(json.dumps(request(port, token, **data)))


if __name__ == "__main__":
    main()
