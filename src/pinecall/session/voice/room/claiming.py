"""The code a caller keys: the last four tones within a window, asked of the gateway as a claim."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from pinecall.session.voice.platform import Platform, PlatformRefused

logger = logging.getLogger(__name__)

# docs/protocol/codes.md: a code is four digits, and the four have to arrive close together — a
# caller keying a code keys it in one go, and a caller answering a menu keys one digit at a time.
LENGTH = 4
WITHIN_S = 8.0

REFUSED = "call %s: the platform refused the claim of a keyed code: %s"


# The worker cannot tell a code from an extension, and does not try: four digits close together
# are ASKED, and the gateway answers whether a page is waiting on them. "Nobody issued that" is
# the common answer and it is nothing; the same four digits are never asked twice on one call.
class Claiming:
    """One call's keypad, heard for the code a page shows: each candidate asked of the gateway."""

    def __init__(
        self, platform: Platform, call: str, length: int = LENGTH, within_s: float = WITHIN_S
    ) -> None:
        self._platform = platform
        self._call = call
        self._length = length
        self._within_s = within_s
        self._keyed: deque[tuple[str, float]] = deque(maxlen=length)
        self._asked: set[str] = set()
        self._claims: set[asyncio.Task[None]] = set()

    def heard(self, digit: str, at: float) -> None:
        """One tone the caller keyed, at that moment; a star or a pound starts a code over."""
        if not digit.isdigit():
            self._keyed.clear()
            return
        self._keyed.append((digit, at))
        if len(self._keyed) < self._length or at - self._keyed[0][1] > self._within_s:
            return
        code = "".join(keyed for keyed, _ in self._keyed)
        self._keyed.clear()
        if code in self._asked:
            return
        self._asked.add(code)
        claim = asyncio.ensure_future(self._claim(code))
        self._claims.add(claim)
        claim.add_done_callback(self._claims.discard)

    def stop(self) -> None:
        """The call is over: a claim still on its way is let go."""
        for claim in self._claims:
            claim.cancel()

    async def _claim(self, code: str) -> None:
        """Ask the gateway; any answer but yes and no is one line, and never the call's end."""
        try:
            await self._platform.claim(self._call, code)
        except PlatformRefused as refused:
            logger.warning(REFUSED, self._call, refused)
