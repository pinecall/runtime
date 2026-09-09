"""~/.pinecall/dev: where a local gateway on a dev key is, so no terminal has to export one."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pinecall._settings import Settings

# The directory is a person's and the file is a key: nobody but this account reads either. The
# tenant's CLI checks the same two things before it trusts what is inside — cli/credentials.ts.
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600

NAME = "dev"


def home() -> Path:
    """The directory the tenant's CLI reads. The one name in this file that is not ours: HOME."""
    return Path.home() / ".pinecall"


def written(settings: Settings, port: int) -> Path | None:
    """Leave this gateway's door where the CLI looks, or take a stale one away. Dev only."""
    path = home() / NAME
    # A box runs on issued keys and a database, never on a dev key, so it writes nothing here —
    # and a file left over from a gateway that used to run on this machine is a lie, so it goes.
    if not settings.dev_key:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    # Created at 0600 rather than written and then narrowed: between the two there is a moment
    # when the key is world-readable, and a key is not something to be careless with for a moment.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump({"url": f"http://127.0.0.1:{port}", "key": settings.dev_key}, file, indent=2)
        file.write("\n")
    # O_CREAT's mode applies only to a file this call created; one already there kept what it had.
    path.chmod(FILE_MODE)
    return path
