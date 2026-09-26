"""Write providers/published_prices.json (the runtime package) from mahimailabs/voice-prices."""

# Run by scripts/refresh-prices. It is a script and not part of the package: nothing at runtime
# fetches anything, and what ships is the file this wrote, reviewable as a diff.

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
WRITTEN = HERE / "src" / "pinecall" / "providers" / "published_prices.json"

REPO = "mahimailabs/voice-prices"
DATA = f"https://raw.githubusercontent.com/{REPO}/main/prices/data_slim.json"
HEAD = f"https://api.github.com/repos/{REPO}/commits/main"

# Their unit to ours. A price is USD, and the `k`/`m` in their names is the quantity it is quoted
# per: `input_kchars` is dollars per thousand characters. Ours are per ONE of the thing a livekit
# usage row counts, because that is what providers/prices.py multiplies.
A_THOUSAND = 1_000
TOKENS = {
    "input_mtok": "input",
    "output_mtok": "output",
    "cache_read_mtok": "cached_input",
    "cache_write_mtok": "cache_creation",
}
MEDIA = {
    "input_kchars": ("characters", A_THOUSAND),
    "input_audio_kseconds": ("audio_seconds", A_THOUSAND),
}


def fetched(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=60) as answer:
        return json.load(answer)


# A tiered price is written as {base, tiers}. The base is the rate every call in this runtime is
# billed at — the tiers start at two hundred thousand tokens of context, which a phone call does
# not reach — so the base is taken and the tiers are dropped, said here and in the file's header.
# Rounded, because the division below turns $0.03 per thousand into 2.9999999999999997e-05 and a
# committed file should read as the number on the vendor's page. Twelve places is far past any
# price and far short of where a float starts lying.
PLACES = 12


def a_number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("base"), int | float):
        return float(value["base"])
    return None


def a_row(model: dict[str, Any]) -> dict[str, Any] | None:
    """One model as this runtime prices one: tokens, or one media unit, or nothing worth keeping."""
    prices = model.get("prices")
    if not isinstance(prices, dict):
        return None
    tokens = {ours: got for their, ours in TOKENS.items() if (got := a_number(prices.get(their)))}
    for their, (unit, per) in MEDIA.items():
        if (got := a_number(prices.get(their))) is not None:
            return {"unit": unit, "usd": round(got / per, PLACES), "as_of": _verified(model)}
    if "input" in tokens and "output" in tokens:
        return {**tokens, "as_of": _verified(model)}
    return None


def _verified(model: dict[str, Any]) -> str:
    return str(model.get("provenance", {}).get("last_verified", ""))


def main() -> int:
    data = fetched(DATA)
    sha = str(fetched(HEAD)["sha"])
    rows: dict[str, dict[str, Any]] = {}
    for provider in data:
        for model in provider.get("models", []):
            name = str(model.get("id", ""))
            row = a_row(model)
            # First writer wins: the file is read in this order and a later vendor that happens to
            # publish the same model id does not silently reprice an earlier one.
            if name and row is not None and name not in rows:
                rows[name] = row
    written = {"source": REPO, "commit": sha, "url": DATA, "prices": rows}
    WRITTEN.write_text(json.dumps(written, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(rows)} models -> {WRITTEN.relative_to(HERE)} (voice-prices {sha[:8]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
