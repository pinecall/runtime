"""The trunk a second leg is dialled out through, asked of the platform only when one is wanted."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

# A call may never dial a second leg — most never do — so the org's outbound trunk is not resolved
# when the room is held. It is asked for the first time a verb needs one, and remembered: a warm
# transfer that rings, fails and is tried again asks the platform once.
type Asking = Callable[[], Awaitable[str | None]]


class Trunks:
    """The org's outbound SIP trunk for this call: asked once, remembered, None when it has none."""

    def __init__(self, asking: Asking) -> None:
        self._asking = asking
        self._asked = False
        self._trunk: str | None = None

    @classmethod
    def known(cls, trunk: str | None) -> Trunks:
        """A trunk already in hand — a dispatch that carried one, a test — and no door to ask."""
        trunks = cls(_none)
        trunks._asked, trunks._trunk = True, trunk
        return trunks

    async def outbound(self) -> str | None:
        """The trunk a second leg dials through, or None when this deployment has none."""
        if not self._asked:
            self._trunk = await self._asking()
            self._asked = True
        return self._trunk


async def _none() -> str | None:
    return None


# What a call that cannot dial out at all is handed: every verb that needs a trunk refuses by name.
NO_TRUNK = Trunks.known(None)
