"""Personas in this process's memory: the port's spec by example, and a gateway with no database."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pinecall.orgs.personas import NOBODY, TAKEN, NameTaken, NoSuchPersona, knob_of


class MemoryPersonas:
    """The same list in this process's own memory: a gateway with no database still simulates."""

    def __init__(self) -> None:
        self._kept: dict[str, dict[str, dict[str, Any]]] = {}

    async def of(self, org: str) -> list[dict[str, Any]]:
        """Every caller of this org, by name."""
        return [dict(one) for _, one in sorted(self._kept.get(org, {}).items())]

    async def named(self, org: str, name: str) -> dict[str, Any] | None:
        """One caller, or None when nobody wrote that name."""
        held = self._kept.get(org, {}).get(name)
        return None if held is None else dict(held)

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
            "llm": knob_of(llm),
            "tts": knob_of(tts),
            "voice": knob_of(voice),
            "accepts_when": accepts_when,
            "declines_when": declines_when,
            "author": author,
            "set_at": time.time(),
        }
        return await self.of(org)

    async def drop(self, org: str, name: str) -> list[dict[str, Any]]:
        """The caller gone, and the list after."""
        held = self._kept.get(org, {})
        if held.pop(name, None) is None:
            raise NoSuchPersona(NOBODY.format(name=name))
        return await self.of(org)
