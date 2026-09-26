"""Personas in Postgres: one row a caller, a rename one statement, read back as the wire says."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pinecall.db import Pool
from pinecall.orgs.personas import NOBODY, TAKEN, NameTaken, NoSuchPersona, knob_of

_LIST = """
SELECT name, about, goal, style, facts, state, llm, tts, voice, accepts_when, declines_when,
       author, set_at
  FROM agent_personas
 WHERE org = $1
 ORDER BY name
"""

# One statement, so a rename is one: the old row goes and the new one arrives together, or
# neither does. Two calls left both names behind whenever anything cut between them. `$9` is the
# name it was called before, NULL for a write that renames nothing — `name = $9` then matches no
# row, as does `$9 <> $2` when the rename is onto the same name, and the DELETE takes nothing.
_PUT = """
WITH gone AS (
    DELETE FROM agent_personas
          WHERE org = $1 AND name = $9 AND $9 <> $2
      RETURNING name
)
INSERT INTO agent_personas (org, name, about, goal, style, facts, state, author, set_at,
                            llm, tts, voice, accepts_when, declines_when)
     VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, now(), $10, $11, $12, $13, $14)
ON CONFLICT (org, name)
  DO UPDATE SET about = EXCLUDED.about,
                goal = EXCLUDED.goal,
                style = EXCLUDED.style,
                facts = EXCLUDED.facts,
                state = EXCLUDED.state,
                llm = EXCLUDED.llm,
                tts = EXCLUDED.tts,
                voice = EXCLUDED.voice,
                accepts_when = EXCLUDED.accepts_when,
                declines_when = EXCLUDED.declines_when,
                author = EXCLUDED.author,
                set_at = now()
"""

_DROP = "DELETE FROM agent_personas WHERE org = $1 AND name = $2 RETURNING name"


class PostgresPersonas:
    """The agent_personas table: every caller an org wrote, by name."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def of(self, org: str) -> list[dict[str, Any]]:
        """Every caller of this org, by name. Each one whole: the gateway keeps no half."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_LIST, org)
        return [_a_persona(row) for row in rows]

    async def named(self, org: str, name: str) -> dict[str, Any] | None:
        """One caller, or None when nobody wrote that name."""
        return next((one for one in await self.of(org) if one["name"] == name), None)

    async def put(
        self,
        org: str,
        name: str,
        *,
        about: str,
        goal: str,
        style: str,
        facts: Mapping[str, str],
        state: Mapping[str, Any],
        author: str,
        was: str | None = None,
        llm: str | None = None,
        tts: str | None = None,
        voice: str | None = None,
        accepts_when: str = "",
        declines_when: str = "",
    ) -> list[dict[str, Any]]:
        """The caller written whole — new, replaced, or renamed from `was` — and the list after.

        A rename is refused before it is written — nobody wrote `was`, or somebody else holds the
        new name — and then it is ONE statement, so no cut leaves the org with both names.
        Nothing is merged: what the page sent IS the caller. The three knobs are NULL when unset
        — the runtime's choice — and the two rules are empty when the caller judges nothing.
        """
        if was is not None and was != name:
            if await self.named(org, was) is None:
                raise NoSuchPersona(NOBODY.format(name=was))
            if await self.named(org, name) is not None:
                raise NameTaken(TAKEN.format(name=name))
        await self._pool.execute(
            _PUT,
            org,
            name,
            about,
            goal,
            style,
            json.dumps(dict(facts)),
            json.dumps(dict(state)),
            author,
            was,
            llm,
            tts,
            voice,
            accepts_when,
            declines_when,
        )
        return await self.of(org)

    async def drop(self, org: str, name: str) -> list[dict[str, Any]]:
        """The caller gone, and the list after. A name nobody wrote is said so, never a quiet no."""
        if await self._pool.fetchrow(_DROP, org, name) is None:
            raise NoSuchPersona(NOBODY.format(name=name))
        return await self.of(org)


# The two JSON columns come back as text from one driver and as objects from another; both are
# read the same way here, so nothing above knows which driver is underneath (db/pool.py).
def _a_persona(row: Mapping[str, Any]) -> dict[str, Any]:
    """One row as the wire's Persona: its words, its two objects, and who wrote it when."""
    return {
        "name": str(row["name"]),
        "about": str(row["about"]),
        "goal": str(row["goal"]),
        "style": str(row["style"]),
        "facts": {str(what): str(said) for what, said in _an_object(row["facts"]).items()},
        "state": _an_object(row["state"]),
        "llm": knob_of(row["llm"]),
        "tts": knob_of(row["tts"]),
        "voice": knob_of(row["voice"]),
        "accepts_when": str(row["accepts_when"]),
        "declines_when": str(row["declines_when"]),
        "author": str(row["author"]),
        "set_at": row["set_at"].timestamp(),
    }


def _an_object(value: Any) -> dict[str, Any]:
    """A JSON column as a mapping, whatever the driver handed back."""
    read: Any = json.loads(value) if isinstance(value, str) else value
    if not isinstance(read, Mapping):
        return {}
    said = cast("Mapping[object, Any]", read)
    return {str(key): held for key, held in said.items()}
