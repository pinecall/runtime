"""Tests for the SFU rebuilt from the tables: the trunks, their numbers and their networks."""

from livekit import api

from pinecall.channels.telephony import numbers, sip
from pinecall.channels.telephony.numbers import NumberImport, NumberPurchase
from pinecall.tenancy import carriers
from tests.channels.conftest import (
    A_NUMBER,
    PEER_NETWORK,
    Line,
    a_peer,
    approved,
    box_sells,
    brought,
)
from tests.conftest import postgres

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
    line.server.dialled.trunks.clear()
    line.server.dialled.rules.clear()
    rebuilt = await sip.rebuild(line.connections)
    assert (rebuilt.numbers, rebuilt.refused) == (3, [])
    assert sorted(line.trunk(line.org).numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.trunk(f"{line.org}:pbx").numbers) == ["+15550100134"]
    assert sorted(line.rule("production").numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.rule("sandbox").numbers) == ["+15550100134"]
    params = len(line.server.dialled.requests)
    await sip.rebuild(line.connections)
    written = [
        item
        for item in line.server.dialled.requests[params:]
        if not isinstance(item, (api.ListSIPInboundTrunkRequest, api.ListSIPDispatchRuleRequest))
    ]
    assert written == []
