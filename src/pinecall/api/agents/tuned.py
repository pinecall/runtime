"""What one session is built on: the corner's tuning and lexicon, read now, over the declaration."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.orgs.tuning import TuningStore
from pinecall.providers.tuning import tuned
from pinecall.types import AgentConfig, Env, Lexicon, Tuning, Versions


@dataclass(frozen=True)
class Tuned:
    """The config the session runs, and which versions it was built on."""

    config: AgentConfig
    versions: Versions


# Read PER SESSION and never held: the store is one indexed row away, calls are seconds apart,
# and what an operator sets on one gateway is on the next call of every other one without any
# gateway being told. The worker reaches this twice for one call — the config hop that builds the
# session, and POST /v1/calls that records the versions — so a set landing in between records n+1
# for a session built on n. Said here, and not cached: a cache is a second place the truth lives.
async def tuned_for(
    kept: TuningStore,
    org: str,
    env: Env,
    holder: str | None,
    slug: str,
    declared: AgentConfig,
) -> Tuned:
    """The corner's newest tuning and lexicon, else the org's own, laid over what the app said."""
    row = await kept.newest(org, env, holder, slug)
    words = await kept.newest_lexicon(org, env, holder)
    config = tuned(
        declared,
        Tuning() if row is None else row.value,
        Lexicon() if words is None else words.value,
    )
    return Tuned(
        config,
        Versions(
            config=None if row is None else row.version,
            lexicon=None if words is None else words.version,
        ),
    )
