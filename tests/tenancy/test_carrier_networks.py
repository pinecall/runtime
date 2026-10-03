"""Tests for the networks an org asks 5060 to open to, and the operator's answer to each."""

import pytest

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy import carrier_networks, orgs
from tests.conftest import postgres


def test_a_network_is_written_one_way_and_a_single_address_is_a_slash_32() -> None:
    assert carrier_networks.checked(" 45.60.12.7 ") == "45.60.12.7/32"
    assert carrier_networks.checked("45.60.12.9/24") == "45.60.12.0/24"


@pytest.mark.parametrize(
    ("network", "why"),
    [
        ("the office", "not an IPv4"),
        ("2001:db8::1", "not an IPv4"),
        ("45.60.0.0/16", "wider than a /24"),
        ("0.0.0.0/0", "wider than a /24"),
        ("10.0.0.0/24", "not a public address"),
        ("192.168.1.0/24", "not a public address"),
        ("100.64.0.0/24", "not a public address"),
        ("127.0.0.1", "not a public address"),
        ("203.0.113.0/24", "not a public address"),
    ],
)
def test_what_cannot_be_admitted_is_refused_saying_why(network: str, why: str) -> None:
    with pytest.raises(DeclarationRefused, match=why):
        carrier_networks.checked(network)


@postgres
async def test_a_peers_networks_wait_and_only_the_approved_ones_count(pool: Pool) -> None:
    org = await orgs.create(pool, "clinica", "Clínica")
    requests = await carrier_networks.ask(pool, org.id, "pbx", ["45.60.12.7", "45.60.13.0/24"])
    assert [(ask.network, ask.state) for ask in requests] == [
        ("45.60.12.7/32", "waiting"),
        ("45.60.13.0/24", "waiting"),
    ]
    assert await carrier_networks.approved(pool, org.id) == {}
    decided = await carrier_networks.decide(pool, requests[0].id, "approved", "ana@pinecall.test")
    assert (decided.state, decided.decided_by) == ("approved", "ana@pinecall.test")
    await carrier_networks.decide(pool, requests[1].id, "refused", "ana@pinecall.test")
    assert await carrier_networks.approved(pool, org.id) == {"pbx": ("45.60.12.7/32",)}
    assert [ask.network for ask in await carrier_networks.listed(pool, "refused")] == [
        "45.60.13.0/24"
    ]
    with pytest.raises(NotFound):
        await carrier_networks.decide(pool, 999_999, "approved", "ana@pinecall.test")


@postgres
async def test_asking_again_keeps_the_answers_and_forgets_what_the_peer_no_longer_names(
    pool: Pool,
) -> None:
    org = await orgs.create(pool, "clinica", "Clínica")
    (first, _) = await carrier_networks.ask(pool, org.id, "pbx", ["45.60.12.7", "45.60.12.8"])
    await carrier_networks.decide(pool, first.id, "approved", "ana@pinecall.test")
    again = await carrier_networks.ask(pool, org.id, "pbx", ["45.60.12.7", "45.60.12.9"])
    assert [(ask.network, ask.state) for ask in again] == [
        ("45.60.12.7/32", "approved"),
        ("45.60.12.9/32", "waiting"),
    ]
    assert len(await carrier_networks.of_org(pool, org.id)) == 2
