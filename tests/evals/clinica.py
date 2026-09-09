"""Clínica Norte as the runtime receives it: the declaration it sends and the prompt it renders."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pinecall.providers.declaration import an_agent, configured
from pinecall.types import AgentConfig, Blocks
from pinecall_protocol import defs

SLUG = "clinica-norte"

# Captured from the class itself, in the agents repository, and copied here as data: the
# declaration the app sends, and the prompt at each of its first states as the tenant's own suite
# pins them byte for byte. State 0 is `identify`: nobody identified, no hours on the table.
# `docs/decisions/evals-rings-1-and-2.md` says why a text ring reads a capture and not a socket.
CAPTURES = Path(__file__).parent / "fixtures" / SLUG
DECLARATION = CAPTURES / "declaration.json"
PROMPTS = CAPTURES

# How `showPrompt` rules the page these captures are (the agents framework's `views/render.ts`):
# one header per block, its region beside its name, and `── history ──` between the regions. Read
# here so that a capture and the ring that drives it can never disagree about where a block ends.
HEADER = re.compile(r"^── (?P<name>[a-z][a-z0-9_]*)(?: \((?:static|dynamic)\))? ──$", re.MULTILINE)
HISTORY = "history"


def declared() -> AgentConfig:
    """The agent as the gateway would hold it, converted by the gateway's own conversion."""
    wire = defs.AgentConfig.model_validate(json.loads(DECLARATION.read_text(encoding="utf-8")))
    return configured(an_agent(SLUG, ()), wire)


# The capture is the tenant's own page, block by block; written into the same blocks, under the
# same layout the declaration carries, it is byte for byte what the model read then.
def prompt_at(state: int) -> Blocks:
    """The captured state N as the app writes it: every block by name, the history left out."""
    captured = (PROMPTS / f"state-{state}.txt").read_text(encoding="utf-8")
    blocks = Blocks(declared().prompt)
    for name, text in _blocks_of(captured).items():
        if name != HISTORY:
            blocks.set(name, text)
    return blocks


def _blocks_of(captured: str) -> dict[str, str]:
    """Each section of the page under its header, with the blank lines around it taken off."""
    headers = list(HEADER.finditer(captured))
    sections: dict[str, str] = {}
    for found, following in zip(headers, headers[1:] + [None], strict=True):
        closes = following.start() if following else len(captured)
        sections[found.group("name")] = captured[found.end() : closes].strip()
    return sections
