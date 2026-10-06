"""A verified key remembered on this gateway for seconds, forgotten once it or its person change."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from pinecall.domain.errors import NotAvailable
from pinecall.postgres.pool import Pool, box_task
from pinecall.process.signal import Signal
from pinecall.tenancy import keys
from pinecall.tenancy.keys import Bearer
from pinecall.tenancy.people import fingerprint

logger = logging.getLogger(__name__)


# Verifying a key is a round trip per request, 15 % of a gateway's core under load (measured
# 2026-09-30). A key is remembered this long; a revocation or a change to its person is said on the
# signal and forgets it everywhere at once, so the seconds are only what a gateway that cannot hear
# the signal keeps, and what a change made outside the doors (a shell, psql) waits.
REMEMBERED_FOR_S = 5.0


# What every gateway hears when a key is revoked, or a person's standing changes (a role, an agent
# list, production, operator, disabled or removed): what it remembered of them no longer holds.
CHANNEL = "keys:forgotten"


RETRY_S = 1.0


class Forgotten(BaseModel):
    """What to forget: one key by its fingerprint, or every key of a person."""

    fingerprint: str | None = None
    subject: str | None = None


@dataclass(frozen=True)
class Remembered:
    """A verified key, and the clock reading it is forgotten at."""

    bearer: Bearer
    until: float


class RememberedKeys:
    """The keys this gateway verified lately, by fingerprint, and the revocations it hears."""

    def __init__(
        self,
        pool: Pool,
        signal: Signal,
        *,
        for_s: float = REMEMBERED_FOR_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Nothing remembered yet; `start` listens for revocations."""
        self.pool = pool
        self.signal = signal
        self.for_s = for_s
        self.clock = clock
        self.kept: dict[str, Remembered] = {}
        self.listening: asyncio.Task[None] | None = None
        self.swept_at = clock()

    async def start(self) -> None:
        """Listen for revocations said by any gateway."""
        if self.listening is None:
            self.listening = box_task(self._listened())

    async def close(self) -> None:
        """Stop listening and forget everything."""
        if self.listening is not None:
            self.listening.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.listening
            self.listening = None
        self.kept.clear()

    async def verify(self, bearer: str) -> Bearer | None:
        """The key and its person, from memory within the seconds, else as keys.verify answers."""
        hashed = fingerprint(bearer)
        now = self.clock()
        remembered = self.kept.get(hashed)
        if remembered is not None and remembered.until > now:
            return remembered.bearer
        verified = await keys.verify(self.pool, bearer)
        if verified is None:
            self.kept.pop(hashed, None)
            return None
        until = now + self.for_s
        if verified.key.expires_at is not None:
            left = (verified.key.expires_at - datetime.now(UTC)).total_seconds()
            until = min(until, now + max(left, 0.0))
        self.kept[hashed] = Remembered(verified, until)
        self._swept(now)
        return verified

    # Forgotten here before the door answers, and said to every other gateway: a signal that is
    # away queues it, so the other gateways keep the key for the seconds and no longer.
    def forget(self, *, fingerprint: str | None = None, subject: str | None = None) -> None:
        """Forget a key, or a person's keys, on every gateway."""
        forgotten = Forgotten(fingerprint=fingerprint, subject=subject)
        self._forgotten(forgotten)
        self.signal.publish(CHANNEL, forgotten.model_dump_json().encode())

    def _forgotten(self, forgotten: Forgotten) -> None:
        if forgotten.fingerprint is not None:
            self.kept.pop(forgotten.fingerprint, None)
        if forgotten.subject is not None:
            for hashed in [h for h, kept in self.kept.items() if _of(kept, forgotten.subject)]:
                del self.kept[hashed]

    # Keys verified once and never again would stay: swept each period.
    def _swept(self, now: float) -> None:
        if now - self.swept_at < self.for_s:
            return
        self.swept_at = now
        for hashed in [h for h, kept in self.kept.items() if kept.until <= now]:
            del self.kept[hashed]

    async def _listened(self) -> None:
        while True:
            try:
                listening = await self.signal.subscribe(CHANNEL)
            except NotAvailable:
                await asyncio.sleep(RETRY_S)
                continue
            # What was revoked while the signal was away is unknown: nothing is trusted from before.
            self.kept.clear()
            try:
                async for data in listening:
                    self._forgotten(Forgotten.model_validate_json(data))
            finally:
                listening.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.signal.connected(), RETRY_S)


def _of(kept: Remembered, subject: str) -> bool:
    return kept.bearer.key.subject == subject
