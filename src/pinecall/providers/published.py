"""The published price list: forty vendors nobody here read a pricing page for, dated per model."""

# providers/prices.py holds the table this runtime read itself — one page per vendor, on a stated
# date, in the units a livekit usage row carries. It covers the five vendors this build runs by
# default and nothing else, which was the whole truth while there were five. The catalog runs to
# every vendor livekit-agents ships a plugin for now (providers/catalog.py), and a call on Cartesia
# that came back `unpriced` is a bill nobody can see.
#
# So behind the curated table sits this one: mahimailabs/voice-prices, "an open, dated source that
# puts direct and gateway cost side by side, per model", vendored as published_prices.json by
# scripts/refresh-prices and committed — nothing at runtime fetches anything, and a price that
# moves arrives as a diff a reviewer can check against the vendor's own page.
#
# What it does NOT carry, on purpose:
#   · the tiered rate above 200k tokens of context — a phone call does not reach it, so the base
#     rate is what is kept and prices.py bills every token at it;
#   · the units this runtime meters nothing in: per-request counts, per-minute telephony, the
#     audio-token pricing of a speech-to-speech model, a voice-clone multiplier. A model priced
#     only in those is left out of the file rather than folded into a number it is not.

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("published_prices.json")


@dataclass(frozen=True)
class Published:
    """The file as it is read: where it came from, and one row per model it prices."""

    source: str
    commit: str
    tokens: dict[str, dict[str, float]]
    media: dict[str, tuple[str, float]]
    dated: dict[str, str]


# Read once per process, off disk, at the first price anybody asks for. The file ships in the
# wheel beside this module: there is no download, no cache to warm and nothing to go stale in a
# way a deploy would not show.
@cache
def published() -> Published:
    """The vendored table, parsed into the two shapes providers/prices.py looks a model up in."""
    data: dict[str, Any] = json.loads(FILE.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, Any]] = data["prices"]
    tokens: dict[str, dict[str, float]] = {}
    media: dict[str, tuple[str, float]] = {}
    dated: dict[str, str] = {}
    for model, row in rows.items():
        dated[model] = str(row.get("as_of", ""))
        if "unit" in row:
            media[model] = (str(row["unit"]), float(row["usd"]))
        else:
            tokens[model] = {name: float(row[name]) for name in row if name != "as_of"}
    return Published(
        source=str(data["source"]),
        commit=str(data["commit"]),
        tokens=tokens,
        media=media,
        dated=dated,
    )


def as_of(model: str) -> str | None:
    """The date somebody last checked this model's price against its vendor's page. None: no row."""
    return published().dated.get(model)
