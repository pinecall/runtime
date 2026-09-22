"""The trunk a second leg dials out through, asked of the platform when a verb wants to dial."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pinecall.session.voice.platform import Dialled

# Asked per dial and never remembered: what comes back is not only the org's trunk but the org's
# guards on THIS number — the shape, how many legs the org has dialled this minute and today — and
# an answer kept from the last verb would be a rate limit that counts to one and stops.
type Asking = Callable[[str], Awaitable[Dialled]]


class Trunks:
    """How a call asks whether it may dial a number, and what to dial it with."""

    def __init__(self, asking: Asking) -> None:
        self._asking = asking

    @classmethod
    def known(cls, trunk: str | None) -> Trunks:
        """A trunk already in hand — a test, a deployment with one — and no door to ask."""

        async def answered(to: str) -> Dialled:  # noqa: ARG001 — the same answer for every number
            return Dialled(trunk=trunk)

        return cls(answered)

    async def outbound(self, to: str) -> Dialled:
        """The trunk to dial `to` with, or the reason this call may not dial it at all."""
        return await self._asking(to)


# What a call that cannot dial out at all is handed: every verb that needs a trunk refuses by name.
NO_TRUNK = Trunks.known(None)
