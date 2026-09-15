"""Key: which of two worlds a key opens (its env), and what it may do there (its scopes)."""

from collections.abc import Iterable
from typing import Literal, get_args

from pinecall.types.refused import DeclarationRefused

# Two worlds and no third. A key is issued into one; the agents registered on it, the doors it
# claims and every call it takes are that world's, and a gateway holds both at once without one
# seeing the other's agents or taking its numbers. What is deployed answers in production; what is
# being written answers in the sandbox.
#
# A third world was very nearly added for `staging`, and it was not needed: whether a sandbox agent
# is ONE PERSON's copy or the team's shared one is not this field, it is whether the key that
# registered it names a person (api/agents/holding.py). A machine key in the sandbox is held by
# nobody's corner, which every member of the org sees — that IS staging, and it already worked.
# Two worlds, three behaviours, and the holder does the third.
#
# The word was `development`, and it was doing two jobs: naming a world, and naming "mine". A team
# that wanted a shared development deployment had nowhere to put it, and a person reading
# `env: development` could not tell a laptop from a box. `sandbox` is what Stripe calls this too.
type Env = Literal["production", "sandbox"]

PRODUCTION: Env = "production"
SANDBOX: Env = "sandbox"
ENVS: frozenset[str] = frozenset(get_args(Env.__value__))

# What a key may do, as the doors are grouped. Every door of the gateway is under exactly one of
# these; a role is a preset of them; a door refuses in a sentence. A key issued before the field
# existed holds every one, which is what `KEY_SCOPES` whole means.
#
#   app         hold an agent over the app socket, and the worker's doors: routes, config, a call
#               opened, appended, sealed, its tools and lookups
#   calls       read a call: its log, its state, its recording, the sessions
#   talk        reach an agent: a room token, a text call, the chat and talk screens
#   supervise   the desk: listen, whisper, say, take over, transfer, end
#   pipeline    what an agent runs on, and the knobs over it
#   knowledge   the knowledge base: push, list, drop, its golden
#   memory      a contact's facts: read, forget, the two goldens
#   evals       the suites, the replays, the runs
#   numbers     which number reaches which agent
#   keys        the org's own API keys: issue one for a machine, list them, revoke one
#   providers   the provider keys an org brought of its own
#   team        the org's members and their invitations
#   usage       what the org consumed
#   fleet       the box's own worker: the fleet's doors, and the worker's doors resolved by the
#               CALL it serves — whose org, which world, whose corner — instead of by this key's
#               org. In no role's preset; only `keys issue --scope fleet` mints it, for a machine
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
    "fleet",
]
# Every word a key may carry, for the verb that checks one; the two sets below are what a key
# is GIVEN, and neither is this whole.
EVERY_SCOPE: frozenset[str] = frozenset(get_args(KeyScope.__value__))

# The worker is the box's, not a tenant's: one process answers every org's calls, so its key names
# no single org's doors. What this scope opens is the call's own corner — the org, the world and
# the holder a dispatch named — at every door the worker knocks; a tenant's key never names
# another's, which is the sentence docs/decisions/keys.md is built on. See auth/keys.py:corner_of.
# It is what `keys issue --scope fleet` mints and NOTHING else: not a role's preset, and not the
# every-scope key below — a key issued with nothing said is a tenant's, and a tenant's key that
# could name another org's corner is the one thing the whole model refuses.
THE_FLEET: KeyScope = "fleet"

# What a key issued with nothing said holds, and what a key issued before the field existed
# holds: every door of its OWN org. Everything but the fleet's.
KEY_SCOPES: frozenset[str] = EVERY_SCOPE - {THE_FLEET}


# Holding an agent is a deployment, and a deployment is a process somebody put on a box — never a
# laptop that happens to be logged in. So a person's key opens `app` in the sandbox, where what
# they run is their own, and never in production, where a slug is held by a key issued for a
# machine (`keys issue --label "prod server" --scope app`). The rule lives at the MINTING and not
# at the door: a door that refused later would have handed out a key promising what it will not do.
HOLDING: KeyScope = "app"

# The members door, which an admin's key and the operator's open and a developer's does not. It is
# what separates "show me the org" from "show me my corner": whoever may see who the team IS may
# also see what the team is RUNNING, so the agent listing asks this one question to decide whose
# sandbox copies a reader is shown. See api/agents/endpoints.py.
THE_TEAM: KeyScope = "team"


# The one question all three of these ask, in the one place it is asked. A deployment is the ORG's:
# a machine holds it, nobody's corner, and every member sees it. The sandbox is a person's: their
# own corner, their own copy. Written as "is it a deployment" rather than "is it production"
# because that is the question — and because the day there are two deployments, this is the line
# that already means the right thing.
def is_a_deployment(env: Env) -> bool:
    """Whether this world is the org's — a machine holds it — rather than one person's sandbox."""
    return env != SANDBOX


def for_a_person(scopes: frozenset[str], env: Env) -> frozenset[str]:
    """What a person may do in this world: their role's preset, less `app` in a deployment."""
    return scopes - {HOLDING} if is_a_deployment(env) else scopes


def an_env(word: str) -> Env:
    """The world this word names, or a refusal that lists the two there are."""
    if word not in ENVS:
        raise DeclarationRefused(f"a key opens one of {sorted(ENVS)}, not {word!r}")
    return "production" if word == PRODUCTION else "sandbox"


def key_scopes(words: Iterable[str]) -> frozenset[str]:
    """The scopes these words name, or a refusal naming the first word that is not one."""
    wanted = frozenset(words)
    for word in sorted(wanted):
        if word not in EVERY_SCOPE:
            raise DeclarationRefused(
                f"{word!r} is not a key scope; the scopes are {sorted(EVERY_SCOPE)}"
            )
    return wanted


# The org's own corner, as the tables that are namespaced by one spell it. Not NULL: it is part of
# a primary key, and a NULL in one matches nothing. A production row is always the org's — a
# person's key opens no `app` there — and so is anything a sandbox key naming nobody wrote,
# which is CI's. See 0021, and api/agents/registry.py for the same idea in the live table.
THE_ORGS_OWN = ""


def whose(holder: str | None) -> str:
    """The corner a row belongs to, as the column spells it. Nobody's is the org's own."""
    return THE_ORGS_OWN if holder is None else holder
