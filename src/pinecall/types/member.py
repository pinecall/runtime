"""Member: a person of one org, the role that presets what their keys may do, and their standing."""

import re
from dataclasses import dataclass
from typing import Literal, get_args

from pinecall.types.key import KEY_SCOPES
from pinecall.types.refused import DeclarationRefused

# A role is a preset of key scopes and nothing more: the doors read scopes, never roles, so a
# role renamed or re-cut tomorrow changes the next key issued and not one door. The five are
# the people a contact center has around an agent: who checks it, who sits beside a live call,
# who runs the floor, who owns the org, and who writes the agent.
type Role = Literal["qa", "supervisor", "manager", "admin", "developer"]
ROLES: frozenset[str] = frozenset(get_args(Role.__value__))

ROLE_SCOPES: dict[str, frozenset[str]] = {
    # Reads finished calls and the suites: the sessions, the scores, drift.
    "qa": frozenset({"calls", "evals"}),
    # Everything qa has, plus the live floor: watch a call, listen, take the line — and what the
    # agent remembers about the caller on it, which a person sitting beside the call has to see.
    # And the words: a supervisor who hears a brand said wrong forty times a day fixes the
    # lexicon, and the opening's words, without a developer and without a deploy.
    "supervisor": frozenset({"calls", "evals", "supervise", "talk", "memory", "words"}),
    # The floor, the org's numbers, keys and consumption, and the words — never which vendor or
    # model the agent runs on, and never its declaration: those are the developer's.
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
    # Every door there is.
    "admin": KEY_SCOPES,
    # Everything a person needs to write and run an agent: the app socket, the pipeline, the
    # knowledge base, a contact's memory, the suites — and the floor, to hear what was written.
    "developer": frozenset(
        {"app", "calls", "talk", "supervise", "pipeline", "knowledge", "memory", "evals", "words"}
    ),
}

# Invited: a row and a one-use token, no password yet. Active: the invitation was accepted and
# the person can log in. Disabled: the person may not log in and their keys were revoked; the
# row stays, because the log names them.
type MemberStatus = Literal["invited", "active", "disabled"]
STATUSES: frozenset[str] = frozenset(get_args(MemberStatus.__value__))

# One `@`, something on both sides, no whitespace: enough to refuse a typo before a row is made.
# What makes an address real is the person accepting the invitation sent to it, not a regex.
_AN_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dataclass(frozen=True)
class Member:
    """One person of one org: who they are, what they may do, which agents, and their standing."""

    id: str
    org: str
    email: str
    name: str
    role: Role
    # Which of the org's agents this person works on. Empty means every one of them.
    agents: frozenset[str] = frozenset()
    status: MemberStatus = "invited"
    # Whether this person runs the BOX, which is a different thing from running their org: their
    # own key opens /v1/ops/* as well as their org's doors. Nobody is one until somebody holding
    # the ops key says so, and an org's `admin` is not one by being an admin — an admin owns a
    # tenant, an operator owns the machine every tenant is on. See 0020.
    operator: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.org:
            raise DeclarationRefused("a member names their id and the org they belong to")
        if not _AN_EMAIL.match(self.email):
            raise DeclarationRefused(f"an email has one @ and a domain, not {self.email!r}")
        if not self.name.strip():
            raise DeclarationRefused("a member has a name: it is what a seat says when they sit")
        if self.role not in ROLES:
            raise DeclarationRefused(f"a role is one of {sorted(ROLES)}, not {self.role!r}")
        if self.status not in STATUSES:
            raise DeclarationRefused(
                f"a member's status is one of {sorted(STATUSES)}, not {self.status!r}"
            )

    @property
    def scopes(self) -> frozenset[str]:
        """What a key minted for this person may do: the role's preset, spelled once above."""
        return ROLE_SCOPES[self.role]


def a_role(word: str) -> Role:
    """The role this word names, or a refusal that lists the five there are."""
    if word not in ROLES:
        raise DeclarationRefused(f"a role is one of {sorted(ROLES)}, not {word!r}")
    return _as_role(word)


def _as_role(word: str) -> Role:
    """The checked word, as the closed type — the one place the cast is made."""
    roles: dict[str, Role] = {
        "qa": "qa",
        "supervisor": "supervisor",
        "manager": "manager",
        "admin": "admin",
        "developer": "developer",
    }
    return roles[word]
