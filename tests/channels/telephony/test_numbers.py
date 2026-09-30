"""Tests for a number imported, bought, released and moved, and one agent at many numbers."""

import base64
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from livekit import api

from pinecall.channels import routes
from pinecall.channels.telephony import dialing, numbers, twilio
from pinecall.channels.telephony.numbers import NumberImport, NumberPurchase
from pinecall.domain.errors import (
    Conflict,
    NotAvailable,
    NotFound,
    QuotaExhausted,
)
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections, vault_of
from pinecall.tenancy import admission, carriers, dial_policy, orgs
from pinecall.tenancy.carriers import TwilioAccount, WhatsappAccount
from pinecall.tenancy.dial_policy import Dial
from tests.channels.conftest import (
    A_NUMBER,
    DOMAIN,
    HER_PHONE,
    HERE,
    THE_OTHER_HALF,
    Line,
    a_peer,
    box_sells,
    brought,
)
from tests.conftest import postgres, settings_of
from tests.fakes.idp import a_sid
from tests.fakes.livekit import Server
from tests.fakes.meta import Graph
from tests.fakes.twilio import Twilio

# ── importing: at the carrier, on the SFU, in the table ──


@postgres
async def test_an_import_makes_the_trunk_attaches_admits_rules_and_routes_once(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    plan = await numbers.import_number(
        line.connections, NumberImport(line.scope("sandbox"), "recepcion", A_NUMBER)
    )
    assert not plan.dry_run
    assert all(step.endswith("done") for step in plan.steps)
    (trunk,) = line.twilio.trunks.values()
    assert trunk.origination == [HERE]
    assert line.twilio.numbers[A_NUMBER][1] == trunk.sid
    sfu = line.trunk(line.org)
    assert list(sfu.numbers) == [A_NUMBER]
    assert list(sfu.allowed_addresses) == list(twilio.TWILIO_SIGNALLING)
    rule = line.rule("sandbox")
    assert list(rule.numbers) == [A_NUMBER]
    assert [agent.agent_name for agent in rule.room_config.agents] == ["pinecall-sandbox"]
    routed = await routes.of_number(line.connections.pool, line.org, A_NUMBER)
    assert routed is not None
    assert (routed.agent, routed.env, routed.managed) == ("recepcion", "sandbox", False)
    again = await numbers.import_number(
        line.connections, NumberImport(line.scope("sandbox"), "recepcion", A_NUMBER)
    )
    assert all(step.endswith("stands") for step in again.steps)
    assert len(line.twilio.trunks) == 1
    assert len(line.server.dialled.trunks) == len(line.server.dialled.rules) == 1


@postgres
async def test_a_dry_run_is_the_plan_and_writes_nothing_anywhere(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    before = len(line.twilio.written())
    plan = await numbers.plan_import(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER)
    )
    assert plan.dry_run
    assert all(step.endswith("to do") for step in plan.steps)
    assert len(line.twilio.written()) == before
    assert line.server.dialled.trunks == {}
    assert await routes.of_number(line.connections.pool, line.org, A_NUMBER) is None


@postgres
async def test_the_trunk_pointing_here_is_found_by_where_it_points_whatever_its_name(
    line: Line,
) -> None:
    await brought(line)
    made_by_hand = line.twilio.trunk("sandbox.pinecall.io", HERE)
    line.twilio.owns(A_NUMBER, trunk=made_by_hand)
    plan = await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER)
    )
    assert plan.steps[0].endswith("stands")
    assert plan.steps[1].endswith("stands")
    assert list(line.twilio.trunks) == [made_by_hand]


@postgres
async def test_a_number_on_another_trunk_of_the_account_is_refused_naming_it_until_moved(
    line: Line,
) -> None:
    await brought(line)
    v1 = line.twilio.trunk("pinecall-org_1", "sip:old.box.test:5060")
    line.twilio.owns(A_NUMBER, trunk=v1)
    with pytest.raises(Conflict, match=r"pinecall-org_1.*old\.box\.test.*move"):
        await numbers.import_number(
            line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER)
        )
    assert line.twilio.numbers[A_NUMBER][1] == v1
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER, move=True)
    )
    assert line.twilio.numbers[A_NUMBER][1] != v1
    assert (
        "DELETE",
        f"/v1/Trunks/{v1}/PhoneNumbers/{line.twilio.numbers[A_NUMBER][0]}",
    ) in line.twilio.requests


@postgres
async def test_a_number_the_account_does_not_own_is_refused_before_anything_is_written(
    line: Line,
) -> None:
    await brought(line)
    with pytest.raises(NotFound, match="not a number of Twilio account"):
        await numbers.import_number(
            line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER)
        )
    assert line.twilio.written() == []


@postgres
async def test_a_number_another_orgs_trunk_lists_is_refused_before_anything_is_written(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await line.server.sip.create_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(name="org_other", numbers=[A_NUMBER])
        )
    )
    with pytest.raises(Conflict, match="another org's trunk"):
        await numbers.import_number(
            line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER)
        )
    assert line.twilio.written() == []


@postgres
async def test_a_peer_touches_nothing_outside_and_rides_its_pair_onto_a_trunk_of_its_own(
    line: Line,
) -> None:
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, a_peer())
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    assert line.twilio.requests == []
    sfu = line.trunk(f"{line.org}:pbx")
    assert (list(sfu.allowed_addresses), sfu.auth_username) == (["203.0.113.0/24"], "pbx")
    routed = await routes.of_number(line.connections.pool, line.org, A_NUMBER)
    assert routed is not None


@postgres
async def test_a_number_the_org_hooks_itself_needs_no_account_and_keeps_its_own_fence(
    line: Line,
) -> None:
    wanted = NumberImport(
        line.scope(), "recepcion", A_NUMBER, hooked=True, networks=("198.51.100.7/32",)
    )
    await numbers.import_number(line.connections, wanted)
    assert list(line.trunk(f"{line.org}:{A_NUMBER}").allowed_addresses) == ["198.51.100.7/32"]
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", "+15550100134", hooked=True)
    )
    assert list(line.trunk(line.org).allowed_addresses) == list(twilio.TWILIO_SIGNALLING)


@postgres
async def test_a_box_with_no_name_cannot_be_pointed_at(line: Line) -> None:
    nameless = replace(line.connections, settings=settings_of(None))
    with pytest.raises(NotAvailable, match="PINECALL_DOMAIN"):
        await numbers.import_number(
            nameless, NumberImport(line.scope(), "recepcion", A_NUMBER, hooked=True)
        )


@postgres
async def test_a_number_imported_again_in_the_other_world_leaves_the_first_worlds_rule(
    line: Line,
) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER, hooked=True)
    )
    await numbers.import_number(
        line.connections, NumberImport(line.scope("sandbox"), "recepcion", A_NUMBER, hooked=True)
    )
    assert line.rules() == [f"{line.org}:sandbox"]
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]


# ── letting go, moving between worlds ──


@postgres
async def test_letting_a_number_go_removes_the_route_and_the_admission_but_not_the_carrier(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    await numbers.release(line.connections, line.org, A_NUMBER, "production")
    assert await routes.of_number(line.connections.pool, line.org, A_NUMBER) is None
    assert line.server.dialled.trunks == {}
    assert line.rules() == []
    assert line.twilio.numbers[A_NUMBER][1] is not None
    with pytest.raises(NotFound):
        await numbers.release(line.connections, line.org, A_NUMBER, "production")


@postgres
async def test_a_number_moved_between_worlds_moves_between_the_rules_and_never_the_trunk(
    line: Line,
) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER, hooked=True)
    )
    trunks = dict(line.server.dialled.trunks)
    moved = await numbers.move(line.connections, line.org, A_NUMBER, "sandbox")
    assert moved.env == "sandbox"
    assert line.rules() == [f"{line.org}:sandbox"]
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]
    assert line.server.dialled.trunks == trunks


@postgres
async def test_a_whatsapp_number_moves_world_by_its_row_and_nothing_lands_on_the_sfu(
    line: Line,
) -> None:
    wanted = NumberImport(line.scope(), "recepcion", A_NUMBER, channel="whatsapp", hooked=True)
    await numbers.import_number(line.connections, wanted)
    moved = await numbers.move(line.connections, line.org, A_NUMBER, "sandbox")
    assert (moved.channel, moved.env) == ("whatsapp", "sandbox")
    assert line.server.dialled.trunks == {}
    assert line.rules() == []


# ── buying on the box's account ──


@postgres
async def test_a_dry_purchase_names_the_number_it_would_buy_and_buys_nothing(line: Line) -> None:
    await box_sells(line, A_NUMBER)
    plan = await numbers.plan_buy(line.connections, NumberPurchase(line.scope(), "recepcion", "us"))
    assert plan.route.number == A_NUMBER
    assert A_NUMBER in plan.steps[0]
    assert line.twilio.written() == []
    assert line.twilio.for_sale == [A_NUMBER]


@postgres
async def test_a_purchase_buys_attaches_admits_and_routes_the_number_as_the_boxs(
    line: Line,
) -> None:
    await box_sells(line, A_NUMBER)
    plan = await numbers.buy_number(
        line.connections, NumberPurchase(line.scope("sandbox"), "recepcion", "us")
    )
    assert plan.route.managed
    assert A_NUMBER in line.twilio.numbers
    assert line.twilio.numbers[A_NUMBER][1] is not None
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]
    assert await routes.managed_in(line.connections.pool, line.org, "sandbox") == 1


@postgres
async def test_the_worlds_stock_of_numbers_caps_purchases_before_twilio_is_asked(
    line: Line,
) -> None:
    await box_sells(line, A_NUMBER, "+15550100134")
    await admission.set_quotas(line.connections.pool, line.org, "production", Quotas(numbers=1))
    await numbers.buy_number(line.connections, NumberPurchase(line.scope(), "recepcion", "us"))
    params = len(line.twilio.requests)
    with pytest.raises(QuotaExhausted, match="numbers"):
        await numbers.buy_number(line.connections, NumberPurchase(line.scope(), "recepcion", "us"))
    assert len(line.twilio.requests) == params


@postgres
async def test_nothing_for_sale_there_is_not_found_in_our_words(line: Line) -> None:
    await box_sells(line)
    with pytest.raises(NotFound, match="no local voice number for sale in US, area code 212"):
        await numbers.buy_number(
            line.connections, NumberPurchase(line.scope(), "recepcion", "us", "212")
        )


@postgres
async def test_a_box_with_no_twilio_of_its_own_buys_for_nobody(line: Line) -> None:
    with pytest.raises(NotAvailable, match="buys no numbers"):
        await numbers.buy_number(line.connections, NumberPurchase(line.scope(), "recepcion", "us"))


# ── one agent, numbers of different kinds from different accounts ──


def either_account(first: Twilio, second: Twilio) -> httpx.MockTransport:
    """A transport that answers as whichever of two Twilio accounts the pair names."""
    theirs = base64.b64encode(f"{second.user}:{second.secret}".encode()).decode()

    def answer(request: httpx.Request) -> httpx.Response:
        found = second if request.headers.get("authorization") == f"Basic {theirs}" else first
        return found.transport().handle_request(request)

    return httpx.MockTransport(answer)


@postgres
async def test_an_agent_answers_at_numbers_of_different_kinds_from_different_accounts(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    first = Twilio()
    second = Twilio(account_sid=a_sid("AC", 2), user=a_sid("SK", 2), secret=THE_OTHER_HALF)
    first.owns(A_NUMBER)
    second.owns("+15550100134")
    server = Server()
    sealed = vault_of(Fernet.generate_key().decode())
    scope = Scope(org, "production")
    async with httpx.AsyncClient(transport=either_account(first, second)) as http:
        connections = Connections(
            settings=settings_of(DOMAIN),
            pool=pool,
            writing=pool,
            vault=sealed,
            http=http,
            server=server,
        )
        for found in (first, second):
            account = TwilioAccount(
                account_sid=found.account_sid, user=found.user, secret=found.secret
            )
            await carriers.put_carrier(connections.pool, connections.vault, org, account)
        with pytest.raises(Conflict, match="2 carrier accounts"):
            await numbers.import_number(connections, NumberImport(scope, "recepcion", A_NUMBER))
        await numbers.import_number(
            connections, NumberImport(scope, "recepcion", A_NUMBER, account=first.account_sid)
        )
        await numbers.import_number(
            connections,
            NumberImport(scope, "recepcion", "+15550100134", account=second.account_sid),
        )
        await numbers.import_number(
            connections,
            NumberImport(scope, "recepcion", "+59899000123", channel="whatsapp", hooked=True),
        )
        kind, owned = await numbers.owned_numbers(connections, scope)
        await dialing.provision_outbound(connections, org, scope.env, second.account_sid)
        await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True))
        leg = await dialing.leg_trunk(
            connections,
            Dial(
                scope, "recepcion", HER_PHONE, "+15550100134", "m_ana", "call_x", datetime.now(UTC)
            ),
        )
    await server.aclose()
    answering = await routes.of_org(pool, org, "production")
    assert [(item.agent, item.channel, item.number) for item in answering] == [
        ("recepcion", "phone", A_NUMBER),
        ("recepcion", "phone", "+15550100134"),
        ("recepcion", "whatsapp", "+59899000123"),
    ]
    assert kind == "twilio"
    assert {item.account for item in owned} == {first.account_sid, second.account_sid}
    assert len(first.trunks) == len(second.trunks) == 1
    assert leg.hostname == twilio.termination_host(DOMAIN, second.account_sid)


# ── what the accounts own ──


@postgres
async def test_an_account_taken_back_leaves_its_numbers_routed(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    await carriers.drop_carrier(line.connections.pool, line.connections.vault, line.org)
    assert [
        item.number for item in await routes.of_org(line.connections.pool, line.org, "production")
    ] == [A_NUMBER]
    with pytest.raises(NotFound, match="no carrier account"):
        await carriers.carrier_named(line.connections.pool, line.connections.vault, line.org)


@postgres
async def test_what_the_accounts_own_is_every_page_and_says_which_this_world_imported(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.page_size = 2
    for last in range(5):
        line.twilio.owns(f"+1555010010{last}")
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", "+15550100102")
    )
    kind, owned = await numbers.owned_numbers(line.connections, line.scope())
    assert kind == "twilio"
    assert len(owned) == 5
    assert [item.number for item in owned if item.imported] == ["+15550100102"]
    sandbox = (await numbers.owned_numbers(line.connections, line.scope("sandbox")))[1]
    assert not any(item.imported for item in sandbox)


@postgres
async def test_a_peer_owns_what_it_owns_and_lists_nothing(line: Line) -> None:
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, a_peer())
    assert await numbers.owned_numbers(line.connections, line.scope()) == ("sip", [])


@postgres
async def test_a_whatsapp_accounts_number_is_listed_from_meta_and_imported_on_the_account(
    line: Line, graph: Graph
) -> None:
    at_meta = WhatsappAccount.model_validate(
        {"phone_number_id": "1055", "access_token": "the org's"}
    )
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, at_meta)
    kind, owned = await numbers.owned_numbers(line.connections, line.scope())
    assert kind == "whatsapp"
    assert [(item.number, item.name, item.account, item.imported) for item in owned] == [
        ("+59829001199", "Clinica", "1055", False)
    ]
    wanted = NumberImport(
        line.scope(), "recepcion", "+59829001199", channel="whatsapp", account="1055"
    )
    plan = await numbers.import_number(line.connections, wanted)
    assert plan.steps == ["route: +59829001199 to recepcion in the production: done"]
    (routed,) = await routes.of_org(line.connections.pool, line.org, "production")
    assert routed.channel == "whatsapp"
    assert (await numbers.owned_numbers(line.connections, line.scope()))[1][0].imported
    graph.refusal = (401, "Session has expired")
    assert (await numbers.owned_numbers(line.connections, line.scope()))[1] == []
