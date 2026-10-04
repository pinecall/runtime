"""Tests for the Twilio trunks moved from a world's name to its SIP name, once."""

from dataclasses import replace

from pinecall.channels.telephony import origination
from tests.channels.conftest import HERE, Line, box_sells, brought
from tests.conftest import postgres

THERE = "sip:sip.box.test:5060;transport=udp"

ELSEWHERE = "sip:another.test:5060;transport=udp"


@postgres
async def test_a_trunk_sent_to_the_worlds_name_is_sent_to_its_sip_name_once(line: Line) -> None:
    await brought(line)
    moved = line.twilio.trunk("box.test", HERE)
    kept = line.twilio.trunk("another box", ELSEWHERE)
    assert await origination.repointed(line.connections) == []
    settings = line.connections.settings.model_copy(update={"sip_domain": "sip.box.test"})
    connections = replace(line.connections, settings=settings)
    done = await origination.repointed(connections)
    assert [(item.trunk, item.world, item.was, item.now) for item in done] == [
        (moved, "production", HERE, THERE)
    ]
    assert line.twilio.trunks[moved].origination == [THERE]
    assert line.twilio.trunks[kept].origination == [ELSEWHERE]
    assert await origination.repointed(connections) == []


# A name retired for another: the trunks still at the old SIP name go to the new one.
@postgres
async def test_a_trunk_at_a_former_name_is_sent_to_the_worlds_sip_name(line: Line) -> None:
    await brought(line)
    retired = "sip:sip.old.test:5060;transport=udp"
    moved = line.twilio.trunk("old box", retired)
    settings = line.connections.settings.model_copy(update={"sip_domain": "sip.box.test"})
    connections = replace(line.connections, settings=settings)
    assert await origination.repointed(connections) == []
    done = await origination.repointed(connections, {"production": ["sip.old.test"]})
    assert [(item.trunk, item.world, item.was, item.now) for item in done] == [
        (moved, "production", retired, THERE)
    ]
    assert line.twilio.trunks[moved].origination == [THERE]


@postgres
async def test_an_account_the_box_and_an_org_both_hold_is_moved_once(line: Line) -> None:
    await brought(line)
    await box_sells(line)
    line.twilio.trunk("box.test", HERE)
    settings = line.connections.settings.model_copy(update={"sip_domain": "sip.box.test"})
    done = await origination.repointed(replace(line.connections, settings=settings))
    assert [item.account for item in done] == [line.twilio.account_sid]
