"""~/.pinecall/dev: where a local gateway on a dev key is, so no terminal has to export one."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# The directory is a person's and the file is a key: nobody but this account reads either. The
# tenant's CLI checks the same two things before it trusts what is inside — cli/credentials.ts.
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600

NAME = "dev"


@dataclass(frozen=True)
class Door:
    """A local gateway on a dev key, as it left itself: where it is, and the one key it takes."""

    url: str
    key: str

    def is_at(self, url: str) -> bool:
        """Whether that url is this gateway, read as the tenant's CLI reads it: origin and path."""
        return _normalised(url) == _normalised(self.url)


def home() -> Path:
    """The directory the tenant's CLI reads. The one name in this file that is not ours: HOME."""
    return Path.home() / ".pinecall"


def written(dev_key: str | None, port: int) -> Path | None:
    """Leave this gateway's door where the CLI looks, or take a stale one away. Dev only."""
    path = home() / NAME
    # A box runs on issued keys and a database, never on a dev key, so it writes nothing here —
    # and a file left over from a gateway that used to run on this machine is a lie, so it goes.
    if not dev_key:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    # Created at 0600 rather than written and then narrowed: between the two there is a moment
    # when the key is world-readable, and a key is not something to be careless with for a moment.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump({"url": f"http://127.0.0.1:{port}", "key": dev_key}, file, indent=2)
        file.write("\n")
    # O_CREAT's mode applies only to a file this call created; one already there kept what it had.
    path.chmod(FILE_MODE)
    return path


# Read by the worker the way the tenant's CLI reads it (cli/env.ts:doorFrom): the file is the word
# of the gateway itself, so a process on this machine that is about to knock at that url knows
# which key it takes without anybody exporting anything. A file that is not JSON, or not ours, is
# no door — the same as none.
def found() -> Door | None:
    """The local gateway that left its door here, or None when no gateway on a dev key is about."""
    try:
        said = json.loads((home() / NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url, key = said.get("url"), said.get("key")
    if not isinstance(url, str) or not isinstance(key, str) or not url or not key:
        return None
    return Door(url=url, key=key)


def _normalised(url: str) -> str:
    """Scheme, host, port and path, with no trailing slash: what makes two spellings one gateway."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
