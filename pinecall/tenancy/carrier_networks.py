"""The networks an org asks the box to open 5060 to, and the operator's answer to each."""

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Network, ip_network
from typing import Literal

from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool

type NetworkState = Literal["waiting", "approved", "refused"]


# A carrier's edge is a few addresses; anything wider is a network nobody vouches for.
WIDEST = 24


NOT_A_NETWORK = "{network} is not an IPv4 address or network, like 198.51.100.7/32"


TOO_WIDE = "{network} is wider than a /{widest}: name the addresses your PBX or carrier calls from"


NOT_PUBLIC = "{network} is not a public address: a carrier reaches the box from the internet"


NO_SUCH_ASK = "no network was asked for with id {ask}"


ASK = """
INSERT INTO carrier_networks (org, source, network) VALUES (%(org)s, %(source)s, %(network)s)
ON CONFLICT (org, source, network) DO NOTHING
"""
FORGET = """
DELETE FROM carrier_networks
WHERE org = %(org)s AND source = %(source)s AND NOT (network = ANY(%(networks)s::cidr[]))
"""
OF_SOURCE = """
SELECT id, org, source, network, state, asked_at, decided_by, decided_at FROM carrier_networks
WHERE org = %(org)s AND source = %(source)s ORDER BY asked_at, network
"""
OF_ORG = """
SELECT id, org, source, network, state, asked_at, decided_by, decided_at FROM carrier_networks
WHERE org = %(org)s ORDER BY asked_at, network
"""
LISTED = """
SELECT id, org, source, network, state, asked_at, decided_by, decided_at FROM carrier_networks
ORDER BY asked_at, id
"""
IN_STATE = """
SELECT id, org, source, network, state, asked_at, decided_by, decided_at FROM carrier_networks
WHERE state = %(state)s ORDER BY asked_at, id
"""
DECIDE = """
UPDATE carrier_networks SET state = %(state)s, decided_by = %(by)s, decided_at = now()
WHERE id = %(id)s RETURNING id, org, source, network, state, asked_at, decided_by, decided_at
"""


@dataclass(frozen=True)
class NetworkAsk:
    """A network an org asked for: what asked, the network, and where the operator stands on it."""

    id: int
    org: str
    source: str
    network: str
    state: NetworkState
    asked_at: float
    decided_by: str | None
    decided_at: float | None


def checked(network: str) -> str:
    """The network written the one way, or DeclarationRefused saying why it cannot be admitted."""
    try:
        parsed = ip_network(network.strip(), strict=False)
    except ValueError:
        raise DeclarationRefused(NOT_A_NETWORK.format(network=network)) from None
    if not isinstance(parsed, IPv4Network):
        raise DeclarationRefused(NOT_A_NETWORK.format(network=network))
    if parsed.prefixlen < WIDEST:
        raise DeclarationRefused(TOO_WIDE.format(network=parsed, widest=WIDEST))
    if not parsed.is_global:
        raise DeclarationRefused(NOT_PUBLIC.format(network=parsed))
    return str(parsed)


def written(network: str) -> str:
    """The network as the table writes it (an address is a /32), or as given when it is none."""
    try:
        return str(ip_network(network.strip(), strict=False))
    except ValueError:
        return network


async def ask(pool: Pool, org: str, source: str, networks: Sequence[str]) -> list[NetworkAsk]:
    """Ask for these networks for the source, forgetting the ones it no longer names; its rows."""
    wanted = [checked(network) for network in networks]
    params = {"org": org, "source": source, "networks": wanted}
    async with pool.connection() as connection, connection.transaction():
        for network in wanted:
            await connection.execute(ASK, {"org": org, "source": source, "network": network})
        await connection.execute(FORGET, params)
        rows = await (await connection.execute(OF_SOURCE, params)).fetchall()
    return [_ask(row) for row in rows]


async def of_org(pool: Pool, org: str) -> list[NetworkAsk]:
    """Every network the org asked for, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG, {"org": org})).fetchall()
    return [_ask(row) for row in rows]


async def approved(pool: Pool, org: str) -> dict[str, tuple[str, ...]]:
    """The networks approved for each source of the org."""
    found: dict[str, tuple[str, ...]] = {}
    for row in await of_org(pool, org):
        if row.state == "approved":
            found[row.source] = (*found.get(row.source, ()), row.network)
    return found


async def listed(pool: Pool, state: NetworkState | None = None) -> list[NetworkAsk]:
    """Every network asked for on the box, or those in one state, oldest first."""
    query, params = (LISTED, {}) if state is None else (IN_STATE, {"state": state})
    async with pool.connection() as connection:
        rows = await (await connection.execute(query, params)).fetchall()
    return [_ask(row) for row in rows]


async def decide(pool: Pool, ask_id: int, state: NetworkState, by: str) -> NetworkAsk:
    """Approve or refuse a network asked for; NotFound for an id nobody asked with."""
    async with pool.connection() as connection:
        row = await (
            await connection.execute(DECIDE, {"id": ask_id, "state": state, "by": by})
        ).fetchone()
    if row is None:
        raise NotFound(NO_SUCH_ASK.format(ask=ask_id))
    return _ask(row)


def _ask(row: DictRow) -> NetworkAsk:
    return NetworkAsk(
        id=int(row["id"]),
        org=str(row["org"]),
        source=str(row["source"]),
        network=str(row["network"]),
        state=row["state"],
        asked_at=row["asked_at"].timestamp(),
        decided_by=row["decided_by"],
        decided_at=None if row["decided_at"] is None else row["decided_at"].timestamp(),
    )
