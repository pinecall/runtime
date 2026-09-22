"""CallContext: what the runtime knows about one call before the first word, for the session."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from pinecall.types.channel import CHANNELS, CHANNELS_WITH_A_NUMBER, DIRECTIONS, Channel, Direction
from pinecall.types.key import Env
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
    # The eval run that opened this call, or None for a person. A run's call opens mid-conversation.
    run: str | None = None
    # The synthetic caller a model is playing on this call, by name (GET /v1/personas), or None
    # when nobody is playing anybody. It rides call.started beside `run` and is projected into
    # call_facts from there: it is what the Personas screen reads a caller's own runs off.
    persona: str | None = None
    # That caller's own rule for the call — when it hangs up satisfied, when unsatisfied — as the
    # persona said it when the call opened, and None for a call nobody wrote one for. It rides
    # call.started beside the name, so the `persona` judge at hang-up, and any judging later,
    # read the rule the call was made under rather than whatever the row says by then.
    accepts_when: str | None = None
    declines_when: str | None = None
    # Whose corner of the world the call is for, as the dispatch that opened it said: a developer
    # in the sandbox, nobody's — the org's own — in production or when nothing said. The org and
    # the world are the route's; this is the third coordinate, and the worker carries it to the
    # door that opens the log so a sandbox call lands in the corner that minted its token.
    holder: str | None = None

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

    # A call is one world's for its whole life, and it is the door's: the route the key declared
    # or the operator typed says which, so call.started carries it without a second field.
    @property
    def env(self) -> Env:
        """The world this call ran in, read off the door it came through."""
        return self.route.env

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
