"""The guards an outbound call passes, in the order that refuses the cheapest thing first."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import override

from pinecall._exceptions import PinecallError
from pinecall.orgs.dialling import Dial, DialPolicies, Dials
from pinecall.types import DeclarationRefused, Destination, DialPolicy, a_destination

# A minute and a day, in seconds: the two windows the ledger is counted over.
A_MINUTE = 60.0
A_DAY = 86_400.0

# Every refusal names the guard in one word, and the word is what the `dials` ledger keeps, so
# "which fence is this org hitting" is one GROUP BY and not a search through sentences.
SHAPE = "shape"
STRANGER = "stranger"
TOO_FAST = "too_fast"
TOO_MANY = "too_many"

NOT_ONE_OF_OURS = (
    "{number} has never called or written to this org: a call back goes back to somebody. An "
    "operator lifts this for the org with dial_anywhere"
)
A_BURST = "org {org} has placed {used} of its {limit} outbound calls a minute: dial.{guard}"
A_DAYS_WORTH = "org {org} has placed {used} of its {limit} outbound calls a day: dial.{guard}"

# What the door answers with. A shape nobody could dial is the caller's mistake (400); a fence is
# this org's standing policy and retrying will not help (403); a window is a wait (429).
STATUS: dict[str, int] = {SHAPE: 400, STRANGER: 403, TOO_FAST: 429, TOO_MANY: 429}


@dataclass(frozen=True)
class Refusal:
    """Which guard said no and what it said. `guard` is the word, `said` is the sentence."""

    guard: str
    said: str

    @property
    def status(self) -> int:
        """The status the door answers with, one per guard and never a judgement call."""
        return STATUS[self.guard]


class DialRefused(PinecallError):
    """A guard said no. `refusal` says which; str() is the sentence the door hands on."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.said)
        self.refusal = refusal

    @property
    def status(self) -> int:
        """What the door answers: the guard's own (api/_refusals.py)."""
        return self.refusal.status

    @override
    def __str__(self) -> str:
        return self.refusal.said


# The two facts the guards need that this package may not go and fetch: whether a number has ever
# reached the org (the call index, log/) and which numbers the org answers at (the routes table,
# routes/). Both are handed in, the way Admission is handed its Counting, so orgs/ keeps importing
# nothing but types and log.
type EverReached = Callable[[str, str], Awaitable[bool]]


# What the door hands the guards: everything a ledger row needs, so one object travels instead of
# six arguments that could be given in the wrong order.
@dataclass(frozen=True)
class Asking:
    """One dial, as the door asked for it: whose, by whom, to where, and shown as what."""

    org: str
    env: str
    agent: str
    to: str
    asked_by: str
    call: str
    shown: str | None = None


@dataclass(frozen=True)
class Allowed:
    """A dial that passed every guard: where it is going, and under which policy it runs."""

    destination: Destination
    policy: DialPolicy


# Ordered so that the cheapest question is asked first and the two that cost a query are asked
# last: a scanner throwing satellite numbers at the door is refused without touching Postgres.
class Guards:
    """Whether this org may dial this number right now, and the ledger row either way."""

    def __init__(
        self,
        policies: DialPolicies,
        dials: Dials,
        ever_reached: EverReached,
    ) -> None:
        self._policies = policies
        self._dials = dials
        self._ever_reached = ever_reached

    async def policy_of(self, org: str) -> DialPolicy:
        """What this org dials under, for a door that reports the guards without passing them."""
        return await self._policies.of(org)

    # Every path through this method writes exactly one ledger row: a dial that was placed carries
    # no `refused`, one that was not carries the guard's word. That is the row the rate guard
    # counts and the row an operator reads when a bill arrives.
    async def judged(self, asking: Asking) -> Allowed:
        """A cold dial, judged: every guard. DialRefused with the guard's own sentence."""
        return await self._judged(asking, fenced=True)

    # A number dialled INTO a call that is already happening — a warm transfer, room.invite — is
    # not a cold call, and the stranger fence is about cold calls: "a call back goes back to
    # somebody". The colleague an agent puts a caller through to has no reason to have ever rung
    # the org, and refusing that by default would leave every tenant choosing between no transfers
    # and `dial_anywhere`, which is the telemarketer's switch. What it does NOT skip is the shape
    # and the two windows: an agent looping a premium-rate number is exactly what those cap, and
    # every leg is a row in the same ledger, so a bill is read back the same way.
    async def a_second_leg(self, asking: Asking) -> Allowed:
        """A leg dialled into a live call: the shape and the windows, not the stranger fence."""
        return await self._judged(asking, fenced=False)

    async def _judged(self, asking: Asking, *, fenced: bool) -> Allowed:
        """Both doors' one body: which guards run is the only thing that differs."""
        try:
            destination = a_destination(asking.to)
        except DeclarationRefused as malformed:
            await self._written(asking, SHAPE)
            raise DialRefused(Refusal(SHAPE, str(malformed))) from malformed
        policy = await self._policies.of(asking.org)
        refusal = await self._fenced(asking.org, destination, policy) if fenced else None
        if refusal is None:
            refusal = await self._paced(asking.org, policy)
        if refusal is not None:
            await self._written(asking, refusal.guard)
            raise DialRefused(refusal)
        await self._written(asking, None)
        return Allowed(destination=destination, policy=policy)

    async def _fenced(
        self, org: str, destination: Destination, policy: DialPolicy
    ) -> Refusal | None:
        """Whether the far end ever reached this org first. Which countries a dial may reach is
        the carrier account's own setting, and never a second fence here."""
        if policy.dial_anywhere:
            return None
        if await self._ever_reached(org, destination.number):
            return None
        return Refusal(STRANGER, NOT_ONE_OF_OURS.format(number=destination.number))

    async def _paced(self, org: str, policy: DialPolicy) -> Refusal | None:
        """The two windows, the short one first: a burst is what an attack looks like."""
        a_minute = await self._dials.since(org, A_MINUTE)
        if a_minute >= policy.per_minute:
            return Refusal(
                TOO_FAST,
                A_BURST.format(org=org, used=a_minute, limit=policy.per_minute, guard=TOO_FAST),
            )
        a_day = await self._dials.since(org, A_DAY)
        if a_day >= policy.per_day:
            return Refusal(
                TOO_MANY,
                A_DAYS_WORTH.format(org=org, used=a_day, limit=policy.per_day, guard=TOO_MANY),
            )
        return None

    async def _written(self, asking: Asking, refused: str | None) -> None:
        """One row per dial asked for, before the call is placed and whatever the answer was."""
        await self._dials.asked(
            Dial(
                org=asking.org,
                env=asking.env,
                agent=asking.agent,
                call=None if refused else asking.call,
                dialled=asking.to,
                shown=asking.shown,
                asked_by=asking.asked_by,
                refused=refused,
            )
        )
