"""The box's settings in this process's memory: brand, mail and sign-in, for as long as it runs."""

from __future__ import annotations

from typing import Any

from pinecall.orgs.box_settings import BoxSetting, opened, sealed
from pinecall.orgs.vault import Cipher


class MemoryBoxSettings:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher | None) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[dict[str, Any], str | None]] = {}

    async def put(self, name: str, value: dict[str, Any], secret: str | None = None) -> None:
        """Sealed here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[name] = (dict(value), sealed(self._cipher, secret))

    async def of(self, name: str) -> BoxSetting | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(name)
        if row is None:
            return None
        value, ciphertext = row
        return BoxSetting(dict(value), opened(self._cipher, ciphertext))

    async def drop(self, name: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(name, None) is not None

    async def noted(self, name: str, changes: dict[str, Any]) -> None:
        """The same merge `||` makes below."""
        row = self._rows.get(name)
        if row is not None:
            self._rows[name] = ({**row[0], **changes}, row[1])
