"""Read a local operator credential shared by the loopback worker and SSH relay."""

import os
from pathlib import Path
import re
import stat


def read_private_token(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(Path(path), flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()):
            raise PermissionError("Expected an operator-owned mode-0600 token file")
        raw = stream.read(129)
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid operator token") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Invalid operator token")
    return token
