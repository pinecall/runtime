"""CallContext: what the runtime knows about one call before the first word, for the session."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from pinecall.types.channel import CHANNELS, CHANNELS_WITH_A_NUMBER, DIRECTIONS, Channel, Direction
from pinecall.types.refused import DeclarationRefused
from pinecall.types.route import Route

# What a call this runtime opens is named: the prefix, then 32 hex. A phone call is named by the
# media plane and a console by livekit; every call we mint ourselves — the chat door, a token —
# is minted here, so the shape is one shape.
A_CALL = "call_"


# Everything is optional: a web visitor may be nobody yet.
@dataclass(frozen=True)
class Contact:
    """Who is on the line, as far as the platform knows."""

    id: str | None = None
    phone: str | None = None
    name: str | None = None
    email: str | None = None
    external_id: str | None = None

    # Memory needs an identity: the number on phone and WhatsApp, a sealed id on the web.
    @property
    def is_known(self) -> bool:
        """True when something about this person can be remembered across calls."""
        return bool(self.id or self.phone or self.external_id)


# Immutable on purpose: what changes during a call is in the log, with a seq, not in here.
@dataclass(frozen=True)
class CallContext:
    """One call as the worker and the gateway see it when it starts."""

    call: str
    channel: Channel
    direction: Direction
    caller: str
    route: Route
    today: date
    contact: Contact | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        if not self.call:
            raise DeclarationRefused("a call context names its call")
        if self.channel not in CHANNELS:
            raise DeclarationRefused(
                f"a call comes through one of {sorted(CHANNELS)}, not {self.channel!r}"
            )
        if self.direction not in DIRECTIONS:
            raise DeclarationRefused(
                f"a call is one of {sorted(DIRECTIONS)}, not {self.direction!r}"
            )
        if not self.caller:
            raise DeclarationRefused("a call has a calling side: a number, or the visitor id")
        if self.route.channel != self.channel:
            raise DeclarationRefused(
                f"a {self.channel} call cannot come through a {self.route.channel} route"
            )

    # The one rule of who a contact is across calls: the id the app resolved when it has one, else
    # the number on the channels that have one — the same person on the phone today and on
    # WhatsApp next week is one contact. A web visitor with no id is nobody yet, and nobody's
    # facts are read or written. docs/decisions/memory.md.
    @property
    def remembered_as(self) -> str | None:
        """The contact memory files this call under, or None when the caller has no identity."""
        if self.contact is not None and self.contact.id:
            return self.contact.id
        return self.caller if self.channel in CHANNELS_WITH_A_NUMBER else None


def a_call_id() -> str:
    """A call nobody has named before. The room a token opens is named by this same call."""
    return f"{A_CALL}{uuid4().hex}"
