"""Where an agent's tuning and the org's lexicon are kept: a row a version, a world, a corner."""

from __future__ import annotations

from pinecall.db import Pool
from pinecall.orgs.tuning_resolution import resolve_tuning
from pinecall.orgs.versions import Corner, Versions
from pinecall.orgs.versions import VersionMoved as VersionMoved
from pinecall.types import Env, Kept, Lexicon, Tuning, whose

# How many versions a history answers when nobody said: a screen's page, not the whole table.
HISTORY_LIMIT = 50


class TuningStore:
    """The two tables — agent_config, one row a version per corner, and lexicon, the org's words —
    over whichever versions they are kept in; the fall-through between corners is here, once."""

    def __init__(self, tunings: Versions[Tuning], lexicons: Versions[Lexicon]) -> None:
        self._tunings = tunings
        self._lexicons = lexicons

    async def newest(
        self, org: str, env: Env, holder: str | None, agent: str
    ) -> Kept[Tuning] | None:
        """What this corner reads: each knob from the nearest corner that sets it, else None."""
        return resolve_tuning(await self._tunings.chain(Corner(org, env, whose(holder), agent)))

    async def own(self, org: str, env: Env, holder: str, agent: str) -> Kept[Tuning] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        return await self._tunings.own(Corner(org, env, holder, agent))

    async def at(
        self, org: str, env: Env, holder: str | None, agent: str, version: int
    ) -> Kept[Tuning] | None:
        """One version, the corner's own if it has it, else the org's own."""
        return await self._tunings.at(Corner(org, env, whose(holder), agent), version)

    async def history(
        self, org: str, env: Env, holder: str, agent: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Tuning]]:
        """This corner's versions, newest first."""
        return await self._tunings.history(Corner(org, env, holder, agent), limit)

    async def every_newest(self, org: str, env: Env, holder: str | None) -> dict[str, Kept[Tuning]]:
        """Every agent in this world by slug, each as this corner reads it."""
        chains = await self._tunings.every_chain(org, env, whose(holder))
        read = {slug: resolve_tuning(chain) for slug, chain in chains.items()}
        return {slug: row for slug, row in read.items() if row is not None}

    async def put(
        self,
        org: str,
        env: Env,
        holder: str,
        agent: str,
        tuning: Tuning,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new version in this corner, numbered after its last; VersionMoved when it moved."""
        corner = Corner(org, env, holder, agent)
        return await self._tunings.put(
            corner, tuning, author=author, note=note, if_version=if_version
        )

    async def newest_lexicon(self, org: str, env: Env, holder: str | None) -> Kept[Lexicon] | None:
        """The corner's own newest lexicon, else the org's own; None when neither set one."""
        chain = await self._lexicons.chain(Corner(org, env, whose(holder)))
        return chain[0] if chain else None

    async def own_lexicon(self, org: str, env: Env, holder: str) -> Kept[Lexicon] | None:
        """This corner's newest lexicon and nothing else's."""
        return await self._lexicons.own(Corner(org, env, holder))

    async def lexicon_at(
        self, org: str, env: Env, holder: str | None, version: int
    ) -> Kept[Lexicon] | None:
        """One version of the lexicon, the corner's own if it has it, else the org's own."""
        return await self._lexicons.at(Corner(org, env, whose(holder)), version)

    async def lexicon_history(
        self, org: str, env: Env, holder: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Lexicon]]:
        """This corner's lexicon versions, newest first."""
        return await self._lexicons.history(Corner(org, env, holder), limit)

    async def put_lexicon(
        self,
        org: str,
        env: Env,
        holder: str,
        lexicon: Lexicon,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new lexicon version in this corner; VersionMoved when the corner moved on."""
        corner = Corner(org, env, holder)
        return await self._lexicons.put(
            corner, lexicon, author=author, note=note, if_version=if_version
        )


def tuning_for(pool: Pool | None) -> TuningStore:
    """The tables when there is a database, and the process's own memory when there is none."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.tuning_store_memory import MemoryTuning
    from pinecall.orgs.tuning_store_postgres import PostgresTuning

    return MemoryTuning() if pool is None else PostgresTuning(pool)
