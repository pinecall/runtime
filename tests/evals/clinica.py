"""Clínica Norte as the runtime receives it: the declaration it sends and the prompt it renders."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pinecall.providers.declaration import an_agent, configured
from pinecall.types import AgentConfig
from pinecall_protocol import defs

SLUG = "clinica-norte"

# Captured from the class itself, in the agents repository, and copied here as data: the
# declaration the app sends, and the prompt at each of its first states as the tenant's own suite
# pins them byte for byte. State 0 is `identify`: nobody identified, no hours on the table.
# `docs/decisions/evals-rings-1-and-2.md` says why a text ring reads a capture and not a socket.
CAPTURES = Path(__file__).parent / "fixtures" / SLUG
DECLARATION = CAPTURES / "declaration.json"
PROMPTS = CAPTURES

# How `showPrompt` writes the three regions (the agents framework's `views/render.ts`). Read here so
# that a capture and the ring that drives it can never disagree about where one region ends.
STATIC_MARKER = "── static ──"
HISTORY_MARKER = "── history ──"
DYNAMIC_MARKER = "── dynamic ──"


@dataclass(frozen=True)
class Prompt:
    """One captured state as the model would read it: the cached prefix and the view."""

    static: str
    view: str


def declared() -> AgentConfig:
    """The agent as the gateway would hold it, converted by the gateway's own conversion."""
    wire = defs.AgentConfig.model_validate(json.loads(DECLARATION.read_text(encoding="utf-8")))
    return configured(an_agent(SLUG, ()), wire)


def prompt_at(state: int) -> Prompt:
    """The static region and the view of the captured state N, as the tenant's suite pins them."""
    captured = (PROMPTS / f"state-{state}.txt").read_text(encoding="utf-8")
    return Prompt(static=_region(captured, STATIC_MARKER, HISTORY_MARKER), view=_dynamic(captured))


def _region(captured: str, opens: str, closes: str) -> str:
    """The text between two region headers, with the blank lines around it taken off."""
    after = captured.split(opens, 1)[1]
    return after.split(closes, 1)[0].strip()


def _dynamic(captured: str) -> str:
    """The last region: it has no header after it, so it runs to the end of the capture."""
    return captured.split(DYNAMIC_MARKER, 1)[1].strip()
