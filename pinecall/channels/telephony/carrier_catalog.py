"""The carriers a box knows (carriers.csv) and which of them its operator admits."""

import csv
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources

from pinecall.domain.errors import Conflict, NotFound
from pinecall.postgres.pool import Pool
from pinecall.process import box_settings

# Each carrier's networks are read from its own page, with the page and the day beside them.
FILE = "carriers.csv"


ADMITTED = "carriers/admitted"


# The box's own carrier: always admitted, its networks are the cloud firewall's own set.
BOX_CARRIER = "twilio"


NO_SUCH_CARRIER = "this box knows no carrier {kind}: one of {known}"


ALWAYS_ADMITTED = "{name} is the box's own carrier: it is always admitted"


@dataclass(frozen=True)
class KnownCarrier:
    """A carrier of the catalog: its published signalling networks, and whether it has an API."""

    kind: str
    name: str
    # The box has a client for its API (lists, points, buys numbers); the rest is SIP terms.
    control: bool
    networks: tuple[str, ...]
    source: str
    read_on: date


@cache
def known() -> dict[str, KnownCarrier]:
    """Every carrier of the shipped catalog, by kind, in the file's order."""
    text = resources.files(__package__).joinpath(FILE).read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    kinds = dict.fromkeys(row["kind"] for row in rows)
    return {
        kind: KnownCarrier(
            kind=kind,
            name=first["name"],
            control=first["control"] == "yes",
            networks=tuple(row["network"] for row in rows if row["kind"] == kind),
            source=first["source"],
            read_on=date.fromisoformat(first["read_on"]),
        )
        for kind in kinds
        for first in [next(row for row in rows if row["kind"] == kind)]
    }


def known_carrier(kind: str) -> KnownCarrier:
    """The catalog's carrier of that kind; NotFound naming the ones there are."""
    found = known().get(kind)
    if found is None:
        raise NotFound(NO_SUCH_CARRIER.format(kind=kind, known=", ".join(known())))
    return found


async def admitted(pool: Pool) -> frozenset[str]:
    """The kinds the operator admits, the box's own carrier always among them."""
    async with pool.connection() as connection:
        value = await box_settings.read(connection, ADMITTED)
    kinds = value.get("kinds") if value is not None else None
    chosen = {BOX_CARRIER}
    for kind in kinds if isinstance(kinds, list) else []:
        if isinstance(kind, str) and kind in known():
            chosen.add(kind)
    return frozenset(chosen)


async def admit(pool: Pool, kind: str, *, on: bool) -> frozenset[str]:
    """Admit the carrier or stop admitting it; the kinds admitted after."""
    carrier = known_carrier(kind)
    if kind == BOX_CARRIER and not on:
        raise Conflict(ALWAYS_ADMITTED.format(name=carrier.name))
    kinds = await admitted(pool)
    after = sorted(kinds | {kind} if on else kinds - {kind})
    async with pool.connection() as connection:
        await box_settings.write(connection, ADMITTED, {"kinds": list(after)})
    return frozenset(after)
