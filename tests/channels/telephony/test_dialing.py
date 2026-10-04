"""Tests for dialling out: provisioning, the guards and their ledger, a call placed, a leg."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from livekit import api

from pinecall.channels import rooms
from pinecall.channels.offers import Offering
from pinecall.channels.telephony import dialing, numbers
from pinecall.channels.telephony._twilio import termination_host
from pinecall.channels.telephony.dialing import Placement
from pinecall.channels.telephony.numbers import NumberImport
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotFound,
    UpstreamFailed,
)
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.tenancy import carriers, dial_policy, orgs
from pinecall.tenancy.carriers import WhatsappAccount
from tests.channels.conftest import (
    A_NUMBER,
    DOMAIN,
    HER_PHONE,
    THE_OTHER_HALF,
    Line,
    a_peer,
    brought,
    dial_of,
    ledger,
)
from tests.conftest import an_offering, postgres
from tests.fakes.idp import a_sid

# ── outbound: the account to dial through ──


# The moment every call here is asked for: noon UTC, inside any hours a +598 number keeps.
NOON = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@postgres
async def test_an_org_with_nothing_is_told_every_step_it_still_owes(line: Line) -> None:
    existing = await dialing.outbound_readiness(line.connections, line.scope())
    assert not existing.ready
    assert existing.steps_missing == [carriers.NO_CARRIER]
    await brought(line)
    existing = await dialing.outbound_readiness(line.connections, line.scope())
    assert len(existing.steps_missing) == 2
    assert "POST /v1/carrier/outbound" in existing.steps_missing[0]
    assert "no phone number" in existing.steps_missing[1]


@postgres
async def test_provisioning_twilio_is_written_once_and_a_second_run_finds_it_standing(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    plan = await dialing.plan_outbound(line.connections, line.org, "production")
    assert plan.dry_run
    assert [step.endswith("to do") for step in plan.steps] == [False, True, True, True, True]
    done = await dialing.provision_outbound(line.connections, line.org, "production")
    (trunk,) = line.twilio.trunks.values()
    assert trunk.domain_name == done.address
    assert str(done.address).endswith(".pstn.twilio.com")
    ((listed, (name, credentials)),) = line.twilio.credential_lists.items()
    assert trunk.credential_lists == [listed]
    assert f"{name}.pstn.twilio.com" == done.address
    assert [username for username, _ in credentials] == [credential_of(line.org)]
    again = await dialing.provision_outbound(line.connections, line.org, "production")
    assert all(step.endswith("stands") for step in again.steps)
    assert len(line.twilio.credential_lists) == 1
    assert (await dialing.outbound_readiness(line.connections, line.scope())).ready


# The box renamed: provisioning gives the trunk its new host, and the org dials where it answers.
@postgres
async def test_a_box_renamed_dials_through_the_host_its_trunk_was_given(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    await dialing.provision_outbound(line.connections, line.org, "production")
    # Its trunk still sends calls where it did: the SIP name is not the name that changed.
    renaming = {"domain": "renamed.test", "sip_domain": DOMAIN}
    settings = line.connections.settings.model_copy(update=renaming)
    renamed = replace(line.connections, settings=settings)
    done = await dialing.provision_outbound(renamed, line.org, "production")
    (trunk,) = line.twilio.trunks.values()
    assert trunk.domain_name == done.address
    assert str(done.address).startswith("renamed-test-")
    carrier = await dialing.carrier_named(renamed.pool, renamed.vault, line.org, None)
    assert carrier.outbound is not None
    assert carrier.outbound.host == done.address


def credential_of(org: str) -> str:
    """The username an org dials a Twilio trunk with."""
    return f"pinecall-{org}".replace("_", "-")


@postgres
async def test_two_orgs_on_one_account_dial_one_trunk_each_with_a_credential_of_its_own(
    line: Line,
) -> None:
    other = await orgs.create(line.connections.pool, "otra", "Otra")
    await brought(line)
    await carriers.put_carrier(
        line.connections.pool, line.connections.vault, other.id, line.account()
    )
    await dialing.provision_outbound(line.connections, line.org, "production")
    await dialing.provision_outbound(line.connections, other.id, "production")
    (trunk,) = line.twilio.trunks.values()
    ((listed, (_, credentials)),) = line.twilio.credential_lists.items()
    assert trunk.credential_lists == [listed]
    assert sorted(username for username, _ in credentials) == sorted(
        [credential_of(line.org), credential_of(other.id)]
    )


@postgres
async def test_a_credential_whose_password_the_box_lost_is_refused_naming_it(
    line: Line,
) -> None:
    await brought(line)
    host = termination_host(DOMAIN, line.twilio.account_sid)
    label = host.removesuffix(".pstn.twilio.com")
    lost = [(credential_of(line.org), "a password nobody here kept")]
    line.twilio.credential_lists[a_sid("CL", 9)] = (label, lost)
    with pytest.raises(Conflict, match="no longer has"):
        await dialing.provision_outbound(line.connections, line.org, "production")


def test_a_peers_networks_are_networks_and_its_outbound_pair_is_whole() -> None:
    with pytest.raises(ValueError, match="not a network"):
        a_peer(addresses=["the office"])
    with pytest.raises(ValueError, match="without outbound_password"):
        a_peer(outbound_username="pinecall-out")
    assert (
        a_peer(outbound_username="out", outbound_password=THE_OTHER_HALF).outbound_username == "out"
    )


@postgres
async def test_a_peer_is_dialled_where_it_said_and_one_that_said_nowhere_is_refused(
    line: Line,
) -> None:
    await carriers.put_carrier(
        line.connections.pool,
        line.connections.vault,
        line.org,
        a_peer(outbound_host="sip.pbx.test", outbound_transport="tls"),
    )
    done = await dialing.provision_outbound(line.connections, line.org, "production")
    assert (done.ready, done.address) == (True, "sip.pbx.test")
    await carriers.put_carrier(
        line.connections.pool, line.connections.vault, line.org, a_peer(username="deaf")
    )
    with pytest.raises(Conflict, match="no outbound_host"):
        await dialing.provision_outbound(line.connections, line.org, "production", "deaf")


# ── placing a call ──


async def ready_to_dial(line: Line) -> None:
    """The org brought its account, imported its number, provisioned it, and dials anyone."""
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    await dialing.provision_outbound(line.connections, line.org, "production")
    await dial_policy.put_guards(
        line.connections.pool, line.org, dial_policy.Guards(dial_anywhere=True)
    )


def dispatching(line: Line) -> Offering:
    """The gateway's dispatcher on the line's pool and server, production's fleet one worker."""
    return an_offering(line.connections.pool, line.server, "pinecall")


def placing(line: Line, shown: str | None = None) -> Placement:
    """A call back to her phone, asked by Ana."""
    return Placement(line.scope(), "recepcion", HER_PHONE, shown, "m_ana", date(2026, 9, 28), NOON)


@postgres
async def test_a_call_placed_opens_its_log_dialing_and_dispatches_its_worlds_fleet(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    logs = Logs(store)
    placed = await dialing.place_call(
        line.connections,
        logs,
        placing(line),
        dispatching(line),
        running=0,
    )
    assert (placed.to, placed.shown) == (HER_PHONE, A_NUMBER)
    (entry,) = await store.whole(placed.call)
    assert entry.type == "call.dialing"
    assert (entry.data["from"], entry.data["to"], entry.data.get("asked_by")) == (
        A_NUMBER,
        HER_PHONE,
        "m_ana",
    )
    (request,) = [
        made_one for made_one in line.server.dispatcher.made if made_one.room == placed.call
    ]
    assert request.agent_name == "pinecall/w1"
    data = rooms.read_dispatch(request.metadata)
    assert data.dial is not None
    assert (data.direction, data.dial.trunk, data.dial.shown) == (
        "outbound",
        line.twilio.account_sid,
        A_NUMBER,
    )
    leg = await dialing.leg_trunk(line.connections, dial_of(line, call=placed.call))
    assert leg.hostname.endswith(".pstn.twilio.com")
    assert leg.username.startswith("pinecall-")


@postgres
async def test_a_number_shown_that_is_not_the_agents_and_an_agent_with_none_are_refused(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    with pytest.raises(DeclarationRefused, match="not a number agent recepcion answers at"):
        await dialing.place_call(
            line.connections,
            Logs(store),
            placing(line, "+34910000000"),
            dispatching(line),
            running=0,
        )
    other = Placement(line.scope(), "agenda", HER_PHONE, None, "m_ana", date(2026, 9, 28), NOON)
    with pytest.raises(NotFound, match="answers at no phone number"):
        await dialing.place_call(
            line.connections,
            Logs(store),
            other,
            dispatching(line),
            running=0,
        )


@postgres
async def test_a_number_the_org_hooked_itself_dials_through_nothing(
    line: Line, store: Store
) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER, hooked=True)
    )
    with pytest.raises(Conflict, match="dials through no account"):
        await dialing.place_call(
            line.connections,
            Logs(store),
            placing(line),
            dispatching(line),
            running=0,
        )


@postgres
async def test_a_dispatch_the_sfu_refuses_ends_the_call_dial_failed_and_seals_it(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    line.server.dispatcher.refusal = api.TwirpError("unavailable", "no fleet", status=503)
    with pytest.raises(UpstreamFailed, match="no fleet"):
        await dialing.place_call(
            line.connections,
            Logs(store),
            placing(line),
            dispatching(line),
            running=0,
        )
    (call,) = [row[2] for row in await ledger(line)]
    assert call is not None
    kinds = [entry.type for entry in await store.whole(call)]
    assert kinds == ["call.dialing", "call.ended"]
    assert await store.sealed(call)


@postgres
async def test_a_whatsapp_number_is_an_account_that_places_no_call(line: Line) -> None:
    await carriers.put_carrier(
        line.connections.pool,
        line.connections.vault,
        line.org,
        WhatsappAccount.model_validate({"phone_number_id": "1055", "access_token": "a token"}),
    )
    existing = await dialing.outbound_readiness(line.connections, line.scope())
    assert "places no call" in existing.steps_missing[0]
