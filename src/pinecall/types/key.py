"""Key: which of two worlds a key opens (its env), and what it may do there (its scopes)."""

from collections.abc import Iterable
from typing import Literal, get_args

from pinecall.types.refused import DeclarationRefused

# Two worlds and no third. A key is issued into one; the agents registered on it, the doors it
# claims and every call it takes are that world's, and a gateway holds both at once without one
# seeing the other's agents or taking its numbers. What is deployed answers in production; what
# is being written answers in development. A staging world would be a third table nobody asked
# for: the thing that tells a deploy from a laptop is the key, and a key has one of these.
type Env = Literal["production", "development"]

PRODUCTION: Env = "production"
DEVELOPMENT: Env = "development"
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
]
KEY_SCOPES: frozenset[str] = frozenset(get_args(KeyScope.__value__))


# Holding an agent is a deployment, and a deployment is a process somebody put on a box — never a
# laptop that happens to be logged in. So a person's key opens `app` in development, where what
# they run is their own, and never in production, where a slug is held by a key issued for a
# machine (`keys issue --label "prod server" --scope app`). The rule lives at the MINTING and not
# at the door: a door that refused later would have handed out a key promising what it will not do.
HOLDING: KeyScope = "app"


def for_a_person(scopes: frozenset[str], env: Env) -> frozenset[str]:
    """What a person may do in this world: their role's preset, less `app` in production."""
    return scopes - {HOLDING} if env == PRODUCTION else scopes


def an_env(word: str) -> Env:
    """The world this word names, or a refusal that lists the two there are."""
    if word not in ENVS:
        raise DeclarationRefused(f"a key opens one of {sorted(ENVS)}, not {word!r}")
    return "production" if word == PRODUCTION else "development"


def key_scopes(words: Iterable[str]) -> frozenset[str]:
    """The scopes these words name, or a refusal naming the first word that is not one."""
    wanted = frozenset(words)
    for word in sorted(wanted):
        if word not in KEY_SCOPES:
            raise DeclarationRefused(
                f"{word!r} is not a key scope; the scopes are {sorted(KEY_SCOPES)}"
            )
    return wanted
