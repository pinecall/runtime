"""Token scopes: the closed set of what a bearer may do, as data the gateway and console read."""

from dataclasses import dataclass
from typing import Literal, get_args

from pinecall.types.refused import DeclarationRefused

type Scope = Literal["talk", "chat", "observe", "supervise", "participate", "read"]

SCOPES: frozenset[str] = frozenset(get_args(Scope.__value__))

# The LiveKit participant attribute a room token carries its scope under. The room publishes it,
# so a worker reads a participant's scope off the media plane without a second lookup.
SCOPE_ATTRIBUTE = "pinecall.scope"

# Who sat down, when the seat was minted from a person's key: the member's id and their name, as
# attributes of the same token, so a verb sent from that seat is written down as theirs. A body
# may not set anything under `pinecall.` (api/tokens.py), so neither can be forged by a browser.
SUBJECT_ATTRIBUTE = "pinecall.subject"
NAME_ATTRIBUTE = "pinecall.name"

# A talk or chat token is minted by the tenant's server for one visit: it opens one session, once,
# and is dead in a minute whether used or not. The browser never mints one. Ten minutes is the most
# a tenant may ask for: a token that lives longer is a door left open on a page nobody is on.
ONE_VISIT_TTL_S = 60
LONGEST_VISIT_TTL_S = 600

# A read token is minted beside a talk or chat token and beside a dial, for the page to follow that
# one call's log and play its recording, before and after it ends. It opens no room. Four hours is
# longer than any call, and short enough that a link copied out of a page is dead by tomorrow.
READ_TTL_S = 4 * 60 * 60

# The projection a read token was minted for, as an attribute of the token: a tenant's server
# chooses it when it mints (`log` on POST /v1/tokens), the page cannot.
PROJECTION_ATTRIBUTE = "pinecall.projection"


@dataclass(frozen=True)
class Grant:
    """What one scope allows, yes or no per field. The gateway checks these, reasons no further."""

    scope: Scope
    connects: bool
    audio: bool
    reads_log: bool
    sends_verbs: bool
    own_call_only: bool
    single_use: bool
    ttl_s: int | None
    # A listener hears the room and is heard by nobody: subscribe without publish, and hidden, so
    # the caller is never told anybody joined. `audio` is both ways; `hears` is the one way.
    hears: bool = False
    hidden: bool = False


GRANTS: dict[str, Grant] = {
    # A talk token carries the participate grant for the call it opens, so a browser needs ONE
    # token to speak and to watch its own call: reads_log, and only its own.
    "talk": Grant(
        "talk",
        connects=True,
        audio=True,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        single_use=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    # A chat token speaks with no microphone, and it HEARS: LiveKit hands text streams — the
    # agent's words on `lk.transcription` — to subscribers only, so a token that could not
    # subscribe typed into the room and never saw the reply (box, 2026-09-16: the call's log had
    # the agent answering, the page had nothing). What it hears is the room; the page attaches
    # no audio, and that is where "audio off" lives now, not in the grant.
    "chat": Grant(
        "chat",
        connects=True,
        audio=False,
        hears=True,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        single_use=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    # An observe token is minted for a LIVE call by the tenant's own door, with the API key
    # (POST /v1/calls/{call}/listen): a supervisor's ear in the room, hidden and silent.
    "observe": Grant(
        "observe",
        connects=False,
        audio=False,
        reads_log=True,
        sends_verbs=False,
        own_call_only=False,
        single_use=False,
        ttl_s=None,
        hears=True,
        hidden=True,
    ),
    # A supervise token is minted for a LIVE call by the tenant's own door too
    # (POST /v1/calls/{call}/supervise), and it is the only scope that sends the verbs. It
    # publishes audio because a supervisor who takes the line has to be heard, and it is NOT
    # hidden: livekit delivers no track from a hidden participant, so a hidden supervisor would
    # take over into a silence. See docs/decisions/tokens.md.
    "supervise": Grant(
        "supervise",
        connects=False,
        audio=True,
        reads_log=True,
        sends_verbs=True,
        own_call_only=False,
        single_use=False,
        ttl_s=None,
    ),
    # A read token follows one call's log and plays its recording, and does nothing else: no
    # room, no verbs. The page holds it for the length of a call and after.
    "read": Grant(
        "read",
        connects=False,
        audio=False,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        single_use=False,
        ttl_s=READ_TTL_S,
    ),
    "participate": Grant(
        "participate",
        connects=False,
        audio=False,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        single_use=False,
        ttl_s=None,
    ),
}


# The scopes a browser holds: each reads the one call it was minted for and nothing past it. This
# is the set the log's guest door and the room's DataChannel both admit, derived and never listed.
READS_ITS_OWN_CALL: frozenset[str] = frozenset(
    scope for scope, grant in GRANTS.items() if grant.reads_log and grant.own_call_only
)

# The scopes a room token may carry through a door of ours: the browser's own, which read the one
# call they were minted for, and the desk's supervise token, minted for one live call and refused
# at every other. Derived, so a scope added tomorrow needs no second list to be remembered in.
BOUND_TO_ONE_CALL: frozenset[str] = READS_ITS_OWN_CALL | frozenset(
    scope for scope, grant in GRANTS.items() if grant.sends_verbs
)

# The scopes POST /v1/tokens mints: the ones that connect to a room. observe and supervise are the
# tenant's own doors and take the API key; participate alone is a call that already exists.
MINTED_FOR_A_VISIT: frozenset[str] = frozenset(
    scope for scope, grant in GRANTS.items() if grant.connects
)


def grant_for(scope: str) -> Grant:
    """The grant behind a scope, or a refusal: a scope nobody declared grants nothing."""
    try:
        return GRANTS[scope]
    except KeyError:
        raise DeclarationRefused(
            f"{scope!r} is not a token scope; the scopes are {sorted(SCOPES)}"
        ) from None
