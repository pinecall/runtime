"""People and their keys: members, roles, invitations, the scopes a key opens."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import AN_ADDRESS, PRODUCTION, Env

# Every gateway door belongs to exactly one scope; `fleet` is the box's worker, resolved per call
# instead of per key org, and only `keys issue --scope fleet` grants it. `runner` is the box's
# own too: the process that runs every org's hosted apps, handed their tokens and secrets.
type KeyScope = Literal[
    "app",
    "calls",
    "talk",
    "supervise",
    "pipeline",
    "knowledge",
    "memory",
    "evals",
    "numbers",
    "keys",
    "providers",
    "team",
    "usage",
    "words",
    "fleet",
    "runner",
]


# A role is only a preset of key scopes. Doors check scopes, never roles, so changing a role
# affects newly issued keys only.
type Role = Literal["qa", "supervisor", "manager", "admin", "developer"]


# A room token's scope, which LiveKit carries on the seat as its `pinecall.scope` attribute, so
# the worker reads it without a lookup.
type RoomScope = Literal["talk", "chat", "observe", "supervise", "participate", "read"]


# disabled: cannot log in and keys are revoked; the row is kept because the log references it.
type MemberStatus = Literal["invited", "active", "disabled"]


@dataclass(frozen=True)
class Member:
    """A person in one org, with their role, agents and status."""

    id: str
    org: str
    email: str
    name: str
    role: Role
    # Empty means all of the org's agents.
    agents: frozenset[str] = frozenset()
    status: MemberStatus = "invited"
    # Box operator: their key also opens /v1/ops/*. Granted only with the ops key.
    operator: bool = False
    # Granted by an admin; checked per request, so revoking it takes effect immediately.
    production: bool = False
    # Email ownership proven by a mailed link, an IdP, or an operator invitation. Required
    # before joining another org without a link.
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.org:
            raise DeclarationRefused("a member names their id and the org they belong to")
        if not AN_ADDRESS.match(self.email):
            raise DeclarationRefused(f"an email has one @ and a domain, not {self.email!r}")
        if not self.name.strip():
            raise DeclarationRefused("a member has a name: it is what a seat says when they sit")

    @property
    def scopes(self) -> frozenset[KeyScope]:
        """Return the scopes of keys minted for this member."""
        return ROLE_SCOPES[self.role]

    @property
    def opens_production(self) -> bool:
        """Return whether this member may act in production; admins always may."""
        return self.role == "admin" or self.production


EVERY_SCOPE: tuple[KeyScope, ...] = (
    "app",
    "calls",
    "talk",
    "supervise",
    "pipeline",
    "knowledge",
    "memory",
    "evals",
    "numbers",
    "keys",
    "providers",
    "team",
    "usage",
    "words",
    "fleet",
    "runner",
)


THE_FLEET: KeyScope = "fleet"


THE_RUNNER: KeyScope = "runner"


HOLDING: KeyScope = "app"


THE_TEAM: KeyScope = "team"


THE_KEYS: KeyScope = "keys"


# Default scopes: every scope except the box's own two.
KEY_SCOPES: frozenset[KeyScope] = frozenset(EVERY_SCOPE) - {THE_FLEET, THE_RUNNER}


# Doors take the org only from this record, never from the request.
@dataclass(frozen=True)
class Key:
    """A verified API key: its org, environment, scopes and holder."""

    key_id: str
    org: str
    label: str | None = None
    env: Env = PRODUCTION
    scopes: frozenset[KeyScope] = KEY_SCOPES
    # The person a key was minted for; None on a server's key. A person's one key opens both
    # worlds, so its env is production's and the request names the world.
    subject: str | None = None
    name: str | None = None
    # None never expires. Keys minted from a personal key never outlive it.
    expires_at: datetime | None = None


ROLES: tuple[Role, ...] = ("qa", "supervisor", "manager", "admin", "developer")


STATUSES: tuple[MemberStatus, ...] = ("invited", "active", "disabled")


# Serving an agent, its calls, room tokens, knowledge pushes and evals; never the org's people,
# numbers or money.
SERVER_SCOPES: frozenset[KeyScope] = frozenset({"app", "calls", "talk", "knowledge", "evals"})


ROLE_SCOPES: Mapping[Role, frozenset[KeyScope]] = {
    "qa": frozenset({"calls", "evals"}),
    "supervisor": frozenset({"calls", "evals", "supervise", "talk", "memory", "words"}),
    "manager": frozenset(
        {
            "calls",
            "evals",
            "supervise",
            "talk",
            "memory",
            "numbers",
            "keys",
            "providers",
            "usage",
            "team",
            "words",
        }
    ),
    "admin": KEY_SCOPES,
    "developer": frozenset(
        {"app", "calls", "talk", "supervise", "pipeline", "knowledge", "memory", "evals", "words"}
    ),
}


# The browser's scopes, limited to their own call: the guest log and the widget's channel admit
# them.
READS_ITS_OWN_CALL: frozenset[RoomScope] = frozenset({"talk", "chat", "read", "participate"})


def key_scopes(words: Iterable[str]) -> frozenset[KeyScope]:
    """Return the words as scopes, raising DeclarationRefused on the first that is none."""
    scopes: set[KeyScope] = set()
    for word in sorted(set(words)):
        if word not in EVERY_SCOPE:
            raise DeclarationRefused(
                f"{word!r} is not a key scope; the scopes are {sorted(EVERY_SCOPE)}"
            )
        scopes.add(word)
    return frozenset(scopes)


def parse_role(word: str) -> Role:
    """Return the word as a Role, raising DeclarationRefused when it is none."""
    if word not in ROLES:
        raise DeclarationRefused(f"a role is one of {sorted(ROLES)}, not {word!r}")
    return word


def parse_status(word: str) -> MemberStatus:
    """Return the word as a member's status, raising DeclarationRefused when it is none."""
    if word not in STATUSES:
        raise DeclarationRefused(f"a member's status is one of {sorted(STATUSES)}, not {word!r}")
    return word
