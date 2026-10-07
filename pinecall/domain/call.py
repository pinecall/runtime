"""What a call is: its id, direction, who is calling, the route it came in by, its day."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import (
    CHANNELS_WITH_A_NUMBER,
    PRODUCTION,
    Channel,
    Direction,
    Env,
    Json,
    dialable,
)

# Prefix of call ids minted here (`call_` + 32 hex); phone calls are named by the media plane.
A_CALL = "call_"

# Who opened a call's log: the fleet's worker, an org's own worker (an app key), or the gateway
# for a written call. The worker doors of a fleet's call take the fleet's key alone.
type Opener = Literal["fleet", "app", "gateway"]


@dataclass(frozen=True)
class Contact:
    """What is known about the caller; a web visitor may be anonymous."""

    id: str | None = None
    phone: str | None = None
    name: str | None = None
    email: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class Route:
    """Which agent answers on a channel and number, and which org owns it."""

    org: str
    agent: str
    channel: Channel
    number: str | None = None
    label: str | None = None
    # A number belongs to one environment; the registry refuses it in the other.
    env: Env = PRODUCTION
    # Bought on the box's carrier account, so it counts against the `numbers` quota; numbers
    # imported from the tenant's own account do not.
    managed: bool = False

    def __post_init__(self) -> None:
        if not self.org or not self.agent:
            raise DeclarationRefused(
                "a route names the org that owns it and the agent that answers"
            )
        if self.channel in CHANNELS_WITH_A_NUMBER:
            if self.number is None or not dialable(self.number):
                raise DeclarationRefused(
                    f"a {self.channel} route answers at a number in E.164 form, not {self.number!r}"
                )
        elif self.number is not None:
            raise DeclarationRefused(f"the {self.channel} widget answers at no number")

    @property
    def door(self) -> tuple[Channel, str | None]:
        """Return (channel, number): the registry's key, whatever the org or the label."""
        return (self.channel, self.number)


# Immutable: whatever changes during a call is recorded in the log.
@dataclass(frozen=True)
class CallContext:
    """A call as the worker and the gateway see it when it starts."""

    call: str
    channel: Channel
    direction: Direction
    caller: str
    route: Route
    today: date
    contact: Contact | None = None
    metadata: Mapping[str, Json] = field(default_factory=dict[str, Json])
    # Eval run that opened the call; None for a real caller.
    run: str | None = None
    # Simulated caller's name, and its rules frozen at call start so judging ignores later edits.
    persona: str | None = None
    accepts_when: str | None = None
    declines_when: str | None = None
    # Sandbox developer the call belongs to; None for the org itself.
    holder: str | None = None
    # The state the call opens in, when a golden, a persona or `?state=` asked for one.
    state: Mapping[str, Json] = field(default_factory=dict[str, Json])

    def __post_init__(self) -> None:
        if not self.call:
            raise DeclarationRefused("a call context names its call")
        if not self.caller:
            raise DeclarationRefused("a call has a calling side: a number, or the visitor id")
        if self.route.channel != self.channel:
            raise DeclarationRefused(
                f"a {self.channel} call cannot come through a {self.route.channel} route"
            )

    @property
    def env(self) -> Env:
        """Return the environment of the call's route."""
        return self.route.env

    # The app's contact id, else the number, so phone and WhatsApp calls share one contact.
    @property
    def remembered_as(self) -> str | None:
        """Return the memory key for the caller, or None when the caller is anonymous."""
        if self.contact is not None and self.contact.id:
            return self.contact.id
        return self.caller if self.channel in CHANNELS_WITH_A_NUMBER else None


# A carrier bills each leg on its own, by the number at the end of it: the box's own number when
# the call came in, the dialled one when it went out.
@dataclass(frozen=True)
class PhoneLeg:
    """One leg of a call on the phone network, and how long it was up."""

    # `twilio`, or `sip` for a trunk the box knows only by its address.
    carrier: str
    direction: Direction
    number: str
    seconds: float


def new_call_id() -> str:
    """Mint a call id; it is also the LiveKit room name."""
    return f"{A_CALL}{uuid4().hex}"


def parse_zone(zone: str) -> ZoneInfo:
    """Return the IANA zone, raising DeclarationRefused when there is none by that name."""
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as unknown:
        raise DeclarationRefused(
            f"{zone!r} is not an IANA time zone (Europe/Madrid, America/Montevideo, UTC)"
        ) from unknown


def today_in(zone: str) -> date:
    """Return today's date in the zone: a call is dated where the caller is, not the box."""
    return datetime.now(UTC).astimezone(parse_zone(zone)).date()
