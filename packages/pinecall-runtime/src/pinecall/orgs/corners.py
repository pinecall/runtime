"""The corners a key reads of the tuning and lexicon: three for a screen, one for a session."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.orgs.tuning_store import TuningStore
from pinecall.providers.tuned_declaration import apply_tuning
from pinecall.types import (
    PRODUCTION,
    THE_ORGS_OWN,
    AgentConfig,
    Env,
    Kept,
    Lexicon,
    Tuning,
    Versions,
)


@dataclass(frozen=True)
class Corners[T]:
    """The three corners as one key sees them, each its own newest and never the fallback."""

    yours: Kept[T] | None
    team: Kept[T] | None
    production: Kept[T] | None


@dataclass(frozen=True)
class Standing:
    """What a corner reads right now — its own newest, else the org's own, else nothing — and
    which versions said so."""

    tuning: Tuning
    lexicon: Lexicon
    versions: Versions


@dataclass(frozen=True)
class Tuned:
    """The config the session runs, and which versions it was built on."""

    config: AgentConfig
    versions: Versions


# `mine` is None for a key that holds no agent — a supervisor's, a manager's — whose own corner
# no session ever resolves in: the screen shows them the team's and production's alone.
async def settings_corners(
    kept: TuningStore, org: str, env: Env, mine: str | None, slug: str
) -> Corners[Tuning]:
    """This agent's tuning in the key's own corner, the team's, and production's."""
    return Corners(
        yours=None if mine is None else await kept.own(org, env, mine, slug),
        team=await kept.own(org, env, THE_ORGS_OWN, slug),
        production=await kept.own(org, PRODUCTION, THE_ORGS_OWN, slug),
    )


async def lexicon_corners(
    kept: TuningStore, org: str, env: Env, mine: str | None
) -> Corners[Lexicon]:
    """The org's words in the key's own corner, the team's, and production's."""
    return Corners(
        yours=None if mine is None else await kept.own_lexicon(org, env, mine),
        team=await kept.own_lexicon(org, env, THE_ORGS_OWN),
        production=await kept.own_lexicon(org, PRODUCTION, THE_ORGS_OWN),
    )


# Read PER SESSION and never held: the store is one indexed row away, calls are seconds apart,
# and what an operator sets on one gateway is on the next call of every other one without any
# gateway being told. The worker reaches this twice for one call — the config hop that builds the
# session, and POST /v1/calls that records the versions — so a set landing in between records n+1
# for a session built on n. Said here, and not cached: a cache is a second place the truth lives.
async def standing_in(
    kept: TuningStore, org: str, env: Env, holder: str | None, slug: str
) -> Standing:
    """The corner's newest settings and lexicon, else the org's own, with their versions."""
    row = await kept.newest(org, env, holder, slug)
    words = await kept.newest_lexicon(org, env, holder)
    return Standing(
        tuning=Tuning() if row is None else row.value,
        lexicon=Lexicon() if words is None else words.value,
        versions=Versions(
            config=None if row is None else row.version,
            lexicon=None if words is None else words.version,
        ),
    )


async def tuned_for(
    kept: TuningStore, org: str, env: Env, holder: str | None, slug: str, declared: AgentConfig
) -> Tuned:
    """The corner's standing settings and lexicon laid on what the app declared."""
    standing = await standing_in(kept, org, env, holder, slug)
    config = apply_tuning(declared, standing.tuning, standing.lexicon)
    return Tuned(config, standing.versions)
