"""Tests for the SFU rebuilt from the tables: the trunks, their numbers and their networks."""

from dataclasses import replace

from livekit import api

from pinecall.channels.rooms import Dispatch, read_dispatch
from pinecall.channels.telephony import numbers, sip
from pinecall.channels.telephony.hand_over import ATTRIBUTE_OF, HAND_OVER, USERNAME, password_of
from pinecall.channels.telephony.numbers import NumberImport, NumberPurchase
from pinecall.process.connections import Connections
from pinecall.tenancy import carriers
from pinecall.wire.rest.numbers import LegTrunk
from tests.channels.conftest import (
    A_NUMBER,
    HER_PHONE,
    PEER_NETWORK,
    Line,
    a_peer,
    approved,
    box_sells,
    brought,
)
from tests.conftest import postgres
from tests.fakes.livekit import A_SECRET

# Production's SIP name, an address already: nothing is asked of a resolver.
PRODUCTION_SIP = "198.51.100.7"

SANDBOX_SIP = "sip.sandbox.box.test"


def named_apart(line: Line) -> Connections:
    """The line's connections with each world's SIP at a name of its own, as a cluster runs it."""
    settings = line.connections.settings.model_copy(
        update={"sip_domain": PRODUCTION_SIP, "sandbox_sip_domain": SANDBOX_SIP}
    )
    return replace(line.connections, settings=settings)


def sharing(connections: Connections) -> Connections:
    """The same connections where the sandbox has no LiveKit of its own."""
    server = connections.servers["production"]
    return replace(connections, servers={"production": server, "sandbox": server})


# ── rebuilding the SFU from the tables ──


@postgres
async def test_an_emptied_sfu_gets_every_trunk_and_both_worlds_rules_back(line: Line) -> None:
    await brought(line)
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, a_peer())
    await approved(line, "pbx", PEER_NETWORK)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(
        line.connections,
        NumberImport(line.scope(), "recepcion", A_NUMBER, account=line.twilio.account_sid),
    )
    await numbers.import_number(
        line.connections,
        NumberImport(line.scope("sandbox"), "recepcion", "+15550100134", account="pbx"),
    )
    await box_sells(line, "+15550100135")
    await numbers.buy_number(line.connections, NumberPurchase(line.scope(), "recepcion", "us"))
    for server in line.servers.values():
        server.dialled.trunks.clear()
        server.dialled.rules.clear()
    rebuilt = await sip.rebuild(line.connections)
    assert (rebuilt.numbers, rebuilt.refused) == (3, [])
    assert sorted(line.trunk(line.org).numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.trunk(f"{line.org}:pbx", "sandbox").numbers) == ["+15550100134"]
    assert sorted(line.rule("production").numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.rule("sandbox").numbers) == ["+15550100134"]
    assert (line.rules("production"), line.rules("sandbox")) == (
        [f"{line.org}:production"],
        [f"{line.org}:sandbox"],
    )
    params = {world: len(server.dialled.requests) for world, server in line.servers.items()}
    await sip.rebuild(line.connections)
    written = [
        item
        for world, server in line.servers.items()
        for item in server.dialled.requests[params[world] :]
        if not isinstance(item, (api.ListSIPInboundTrunkRequest, api.ListSIPDispatchRuleRequest))
    ]
    assert written == []


@postgres
async def test_the_rebuild_takes_a_number_off_the_livekit_of_the_world_it_is_not_in(
    line: Line,
) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope("sandbox"), "recepcion", A_NUMBER, hooked=True)
    )
    # Admitted on production's LiveKit before the sandbox had one of its own.
    stale = line.servers["production"]
    for item in line.servers["sandbox"].dialled.trunks.values():
        await stale.sip.create_inbound_trunk(api.CreateSIPInboundTrunkRequest(trunk=item))
    for item in line.servers["sandbox"].dialled.rules.values():
        await stale.sip.create_dispatch_rule(api.CreateSIPDispatchRuleRequest(dispatch_rule=item))
    assert line.rules("production") == [f"{line.org}:sandbox"]
    await sip.rebuild(line.connections)
    assert stale.dialled.trunks == stale.dialled.rules == {}
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]


@postgres
async def test_a_rebuild_while_a_livekit_starts_is_tried_again_until_its_numbers_stand(
    line: Line,
) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope("sandbox"), "recepcion", A_NUMBER, hooked=True)
    )
    for server in line.servers.values():
        server.dialled.trunks.clear()
        server.dialled.rules.clear()
    line.servers["sandbox"].dialled.starting = 2
    rebuilt = await sip.rebuilt_until_whole(line.connections, wait_s=0.0)
    assert (rebuilt.numbers, rebuilt.refused) == (1, [])
    assert line.servers["sandbox"].dialled.starting == 0
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]


# ── rings handed over from production ──


@postgres
async def test_the_sandboxs_own_livekit_admits_hand_overs_from_productions_address_alone(
    line: Line,
) -> None:
    connections = named_apart(line)
    assert (await sip.rebuild(connections)).hand_over
    trunk = line.trunk(HAND_OVER, "sandbox")
    assert (list(trunk.numbers), list(trunk.allowed_addresses)) == ([], [PRODUCTION_SIP])
    assert (trunk.auth_username, trunk.auth_password) == (USERNAME, password_of(A_SECRET))
    assert dict(trunk.headers_to_attributes) == dict(ATTRIBUTE_OF)
    (rule,) = line.servers["sandbox"].dialled.rules.values()
    (sent,) = rule.room_config.agents
    assert (rule.name, list(rule.trunk_ids)) == (HAND_OVER, [trunk.sip_trunk_id])
    assert (sent.agent_name, read_dispatch(sent.metadata)) == (
        "pinecall-sandbox",
        Dispatch(env="sandbox", diverted_from="production"),
    )
    production = line.servers["production"].dialled
    assert production.trunks == production.rules == {}
    before = len(line.servers["sandbox"].dialled.requests)
    await sip.rebuild(connections)
    written = [
        item
        for item in line.servers["sandbox"].dialled.requests[before:]
        if not isinstance(item, (api.ListSIPInboundTrunkRequest, api.ListSIPDispatchRuleRequest))
    ]
    assert written == []


@postgres
async def test_nothing_is_admitted_where_the_worlds_share_a_livekit_or_production_has_no_sip_name(
    line: Line,
) -> None:
    assert not (await sip.rebuild(sharing(named_apart(line)))).hand_over
    assert not (await sip.rebuild(line.connections)).hand_over
    assert all(server.dialled.trunks == {} for server in line.servers.values())


@postgres
async def test_a_ring_is_dialled_to_the_sandboxs_sip_only_where_it_has_a_livekit_of_its_own(
    line: Line,
) -> None:
    connections = named_apart(line)
    assert sip.hand_over_trunk(connections, HER_PHONE) == LegTrunk(
        hostname=SANDBOX_SIP,
        transport="udp",
        username=USERNAME,
        password=password_of(A_SECRET),
        shown=HER_PHONE,
    )
    assert sip.hand_over_trunk(sharing(connections), HER_PHONE) is None
