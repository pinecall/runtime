"""Where an org's synthetic callers are kept: one row a caller, one list an org, whichever agent."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast, override

from pinecall._exceptions import PinecallError
from pinecall.log.store import Pool

# A caller nobody wrote, and a name taken by somebody else: the two things a write can meet.
NOBODY = "no persona called {name} in this org"
TAKEN = "this org has a persona called {name} already"


class NoSuchPersona(PinecallError):
    """The caller a read, a rename or a drop named is not one this org has."""


class NameTaken(PinecallError):
    """A rename onto a name another caller of this org already holds."""


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


class Personas:
    """Every caller an org wrote, read and written by name — whichever agent ends up answering."""

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
# read the same way here, so nothing above knows which driver is underneath (log/store/pool.py).
def _a_persona(row: Mapping[str, Any]) -> dict[str, Any]:
    """One row as the wire's Persona: its words, its two objects, and who wrote it when."""
    return {
        "name": str(row["name"]),
        "about": str(row["about"]),
        "goal": str(row["goal"]),
        "style": str(row["style"]),
        "facts": {str(what): str(said) for what, said in _an_object(row["facts"]).items()},
        "state": _an_object(row["state"]),
        "llm": _a_knob(row["llm"]),
        "tts": _a_knob(row["tts"]),
        "voice": _a_knob(row["voice"]),
        "accepts_when": str(row["accepts_when"]),
        "declines_when": str(row["declines_when"]),
        "author": str(row["author"]),
        "set_at": row["set_at"].timestamp(),
    }


def _a_knob(value: Any) -> str | None:
    """One of the three knobs as the wire says it: the word that was set, or None for unset."""
    return None if value is None or value == "" else str(value)


def _an_object(value: Any) -> dict[str, Any]:
    """A JSON column as a mapping, whatever the driver handed back."""
    read: Any = json.loads(value) if isinstance(value, str) else value
    if not isinstance(read, Mapping):
        return {}
    said = cast("Mapping[object, Any]", read)
    return {str(key): held for key, held in said.items()}


class MemoryPersonas(Personas):
    """The same list in this process's own memory: a gateway with no database still simulates."""

    def __init__(self) -> None:  # noqa: D107 — there is nothing to open
        self._kept: dict[str, dict[str, dict[str, Any]]] = {}

    @override
    async def of(self, org: str) -> list[dict[str, Any]]:
        """Every caller of this org, by name."""
        return [dict(one) for _, one in sorted(self._kept.get(org, {}).items())]

    @override
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
        """The caller written whole, renamed from `was` when it is one, and the list after."""
        held = self._kept.setdefault(org, {})
        if was is not None and was != name:
            if was not in held:
                raise NoSuchPersona(NOBODY.format(name=was))
            if name in held:
                raise NameTaken(TAKEN.format(name=name))
            del held[was]
        held[name] = {
            "name": name,
            "about": about,
            "goal": goal,
            "style": style,
            "facts": dict(facts),
            "state": dict(state),
            "llm": _a_knob(llm),
            "tts": _a_knob(tts),
            "voice": _a_knob(voice),
            "accepts_when": accepts_when,
            "declines_when": declines_when,
            "author": author,
            "set_at": time.time(),
        }
        return await self.of(org)

    @override
    async def drop(self, org: str, name: str) -> list[dict[str, Any]]:
        """The caller gone, and the list after."""
        held = self._kept.get(org, {})
        if held.pop(name, None) is None:
            raise NoSuchPersona(NOBODY.format(name=name))
        return await self.of(org)


def personas_for(pool: Pool | None) -> Personas:
    """The table when there is a database, and this process's own memory when there is none."""
    return MemoryPersonas() if pool is None else Personas(pool)
