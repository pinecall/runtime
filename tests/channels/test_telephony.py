"""Tests for carrier accounts, numbers hooked and admitted on the SFU, buying, and dialling out."""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from livekit import api

from pinecall.channels import routes, telephony
from pinecall.channels.telephony import (
    Asking,
    Exchange,
    Import,
    Placing,
    Purchase,
    SipPeer,
    TwilioAccount,
    WhatsappAccount,
)
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    QuotaExhausted,
    UpstreamFailed,
)
from pinecall.domain.types import Corner, Env, Quotas
from pinecall.log.log import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import orgs, vault
from tests.conftest import postgres
from tests.fakes import Server, Twilio, a_sid

DOMAIN = "box.test"
HERE = "sip:box.test:5060;transport=udp"
A_NUMBER = "+15550100133"
# What a peer dials out with beside its username.
THE_OTHER_HALF = "the other half of the pair"
HER_PHONE = "+59899000001"
NFTABLES = Path(__file__).parents[2] / "infra/box/nftables.conf"


@dataclass
class Line:
    """The exchange a test hooks numbers through, the org, and the fakes behind it."""

    exchange: Exchange
    org: str
    twilio: Twilio
    server: Server

    def corner(self, env: Env = "production") -> Corner:
        """The org's corner in the world."""
        return Corner(self.org, env)

    def account(self) -> TwilioAccount:
        """The fake account, as the org brings it."""
        return TwilioAccount(
            account_sid=self.twilio.account_sid, user=self.twilio.user, secret=self.twilio.secret
        )

    def rule(self, env: Env) -> api.SIPDispatchRuleInfo:
        """The org's rule of the world on the SFU."""
        return next(
            one for one in self.server.dialled.rules.values() if one.name == f"{self.org}:{env}"
        )

    def rules(self) -> list[str]:
        """The names of the rules on the SFU."""
        return sorted(one.name for one in self.server.dialled.rules.values())

    def trunk(self, name: str) -> api.SIPInboundTrunkInfo:
        """A trunk on the SFU by name."""
        return next(one for one in self.server.dialled.trunks.values() if one.name == name)


@pytest.fixture
async def line(pool: Pool) -> AsyncIterator[Line]:
    """An org, a Twilio account nobody brought yet, an SFU with nothing on it."""
    org = await orgs.create(pool, "clinica", "Clinica")
    twilio = Twilio()
    server = Server()
    async with httpx.AsyncClient(transport=twilio.transport()) as http:
        sealed = vault.vault_of(Fernet.generate_key().decode())
        yield Line(Exchange(pool, sealed, http, server, DOMAIN), org.id, twilio, server)
    await server.aclose()


async def brought(line: Line) -> None:
    """The org brought its Twilio account."""
    await telephony.bring(line.exchange, line.org, line.account())


def a_peer(**said: object) -> SipPeer:
    """A PBX of the org's that calls from its office's network."""
    return SipPeer.model_validate(
        {
            "username": "pbx",
            "password": "a peer's password",
            "addresses": ["203.0.113.0/24"],
            **said,
        }
    )


# ── the accounts ──


@postgres
async def test_a_twilio_account_is_verified_kept_sealed_and_never_its_secret_in_the_row(
    line: Line,
) -> None:
    await brought(line)
    async with line.exchange.pool.connection() as connection:
        row = await (await connection.execute("select * from carriers")).fetchone()
    assert row is not None
    assert (row["kind"], row["account"]) == ("twilio", line.twilio.account_sid)
    assert line.twilio.secret not in str(row["ciphertext"])
    held = await telephony.carrier(line.exchange.pool, line.exchange.vault, line.org)
    assert held.account == line.account()


@postgres
async def test_a_pair_twilio_refuses_is_refused_in_our_words_and_nothing_is_kept(
    line: Line,
) -> None:
    wrong = line.account().model_copy(update={"secret": "not it"})
    with pytest.raises(DeclarationRefused, match="Twilio refused"):
        await telephony.bring(line.exchange, line.org, wrong)
    assert await telephony.carriers_of(line.exchange.pool, line.exchange.vault, line.org) == []


def test_a_sid_that_is_not_one_is_refused_before_twilio_is_asked() -> None:
    with pytest.raises(ValueError, match="account_sid"):
        TwilioAccount.model_validate(
            {"account_sid": "AC123", "user": a_sid("SK", 2), "secret": "s"}
        )


def test_a_peers_networks_are_given_and_a_kind_nobody_knows_is_refused() -> None:
    with pytest.raises(ValueError, match="addresses"):
        SipPeer.model_validate({"username": "pbx", "password": "p", "addresses": []})
    with pytest.raises(ValueError, match="kind"):
        telephony.ACCOUNT.validate_python({"kind": "telegraph"})


@postgres
async def test_an_org_holds_many_accounts_and_one_is_named_when_there_are_several(
    line: Line,
) -> None:
    await brought(line)
    await telephony.bring(line.exchange, line.org, a_peer())
    held = await telephony.carriers_of(line.exchange.pool, line.exchange.vault, line.org)
    assert [one.id for one in held] == [line.twilio.account_sid, "pbx"]
    with pytest.raises(Conflict, match="2 carrier accounts"):
        await telephony.carrier(line.exchange.pool, line.exchange.vault, line.org)
    peer = await telephony.carrier(line.exchange.pool, line.exchange.vault, line.org, "pbx")
    assert isinstance(peer.account, SipPeer)


@postgres
async def test_an_account_taken_back_leaves_its_numbers_routed(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    await telephony.take_back(line.exchange.pool, line.exchange.vault, line.org)
    assert [
        one.number for one in await routes.of_org(line.exchange.pool, line.org, "production")
    ] == [A_NUMBER]
    with pytest.raises(NotFound, match="no carrier account"):
        await telephony.carrier(line.exchange.pool, line.exchange.vault, line.org)


@postgres
async def test_what_the_accounts_own_is_every_page_and_says_which_this_world_imported(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.page_size = 2
    for last in range(5):
        line.twilio.owns(f"+1555010010{last}")
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", "+15550100102"))
    kind, owned = await telephony.available(line.exchange, line.corner())
    assert kind == "twilio"
    assert len(owned) == 5
    assert [one.number for one in owned if one.imported] == ["+15550100102"]
    sandbox = (await telephony.available(line.exchange, line.corner("sandbox")))[1]
    assert not any(one.imported for one in sandbox)


@postgres
async def test_a_peer_owns_what_it_owns_and_lists_nothing(line: Line) -> None:
    await telephony.bring(line.exchange, line.org, a_peer())
    assert await telephony.available(line.exchange, line.corner()) == ("sip", [])


# ── importing: at the carrier, on the SFU, in the table ──


@postgres
async def test_an_import_makes_the_trunk_attaches_admits_rules_and_routes_once(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    plan = await telephony.import_number(
        line.exchange, Import(line.corner("sandbox"), "recepcion", A_NUMBER)
    )
    assert not plan.dry_run
    assert all(step.endswith("done") for step in plan.steps)
    (trunk,) = line.twilio.trunks.values()
    assert trunk.origination == [HERE]
    assert line.twilio.numbers[A_NUMBER][1] == trunk.sid
    sfu = line.trunk(line.org)
    assert list(sfu.numbers) == [A_NUMBER]
    assert list(sfu.allowed_addresses) == list(telephony.TWILIO_SIGNALLING)
    rule = line.rule("sandbox")
    assert list(rule.numbers) == [A_NUMBER]
    assert [one.agent_name for one in rule.room_config.agents] == ["pinecall-sandbox"]
    routed = await routes.of_number(line.exchange.pool, line.org, A_NUMBER)
    assert routed is not None
    assert (routed.agent, routed.env, routed.managed) == ("recepcion", "sandbox", False)
    again = await telephony.import_number(
        line.exchange, Import(line.corner("sandbox"), "recepcion", A_NUMBER)
    )
    assert all(step.endswith("stands") for step in again.steps)
    assert len(line.twilio.trunks) == 1
    assert len(line.server.dialled.trunks) == len(line.server.dialled.rules) == 1


@postgres
async def test_a_dry_run_is_the_plan_and_writes_nothing_anywhere(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    before = len(line.twilio.written())
    plan = await telephony.plan_import(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    assert plan.dry_run
    assert all(step.endswith("to do") for step in plan.steps)
    assert len(line.twilio.written()) == before
    assert line.server.dialled.trunks == {}
    assert await routes.of_number(line.exchange.pool, line.org, A_NUMBER) is None


@postgres
async def test_the_trunk_pointing_here_is_found_by_where_it_points_whatever_its_name(
    line: Line,
) -> None:
    await brought(line)
    made_by_hand = line.twilio.trunk("sandbox.pinecall.io", HERE)
    line.twilio.owns(A_NUMBER, trunk=made_by_hand)
    plan = await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER)
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
        await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    assert line.twilio.numbers[A_NUMBER][1] == v1
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER, move=True)
    )
    assert line.twilio.numbers[A_NUMBER][1] != v1
    assert (
        "DELETE",
        f"/v1/Trunks/{v1}/PhoneNumbers/{line.twilio.numbers[A_NUMBER][0]}",
    ) in line.twilio.asked


@postgres
async def test_a_number_the_account_does_not_own_is_refused_before_anything_is_written(
    line: Line,
) -> None:
    await brought(line)
    with pytest.raises(NotFound, match="not a number of Twilio account"):
        await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
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
        await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    assert line.twilio.written() == []


@postgres
async def test_a_peer_touches_nothing_outside_and_rides_its_pair_onto_a_trunk_of_its_own(
    line: Line,
) -> None:
    await telephony.bring(line.exchange, line.org, a_peer())
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    assert line.twilio.asked == []
    sfu = line.trunk(f"{line.org}:pbx")
    assert (list(sfu.allowed_addresses), sfu.auth_username) == (["203.0.113.0/24"], "pbx")
    routed = await routes.of_number(line.exchange.pool, line.org, A_NUMBER)
    assert routed is not None


@postgres
async def test_a_number_the_org_hooks_itself_needs_no_account_and_keeps_its_own_fence(
    line: Line,
) -> None:
    wanted = Import(
        line.corner(), "recepcion", A_NUMBER, hooked=True, networks=("198.51.100.7/32",)
    )
    await telephony.import_number(line.exchange, wanted)
    assert list(line.trunk(f"{line.org}:{A_NUMBER}").allowed_addresses) == ["198.51.100.7/32"]
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", "+15550100134", hooked=True)
    )
    assert list(line.trunk(line.org).allowed_addresses) == list(telephony.TWILIO_SIGNALLING)


@postgres
async def test_a_box_with_no_name_cannot_be_pointed_at(line: Line) -> None:
    nameless = Exchange(
        line.exchange.pool, line.exchange.vault, line.exchange.http, line.server, None
    )
    with pytest.raises(NotAvailable, match="PINECALL_DOMAIN"):
        await telephony.import_number(
            nameless, Import(line.corner(), "recepcion", A_NUMBER, hooked=True)
        )


@postgres
async def test_a_number_imported_again_in_the_other_world_leaves_the_first_worlds_rule(
    line: Line,
) -> None:
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER, hooked=True)
    )
    await telephony.import_number(
        line.exchange, Import(line.corner("sandbox"), "recepcion", A_NUMBER, hooked=True)
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
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    await telephony.release(line.exchange, line.org, A_NUMBER, "production")
    assert await routes.of_number(line.exchange.pool, line.org, A_NUMBER) is None
    assert line.server.dialled.trunks == {}
    assert line.rules() == []
    assert line.twilio.numbers[A_NUMBER][1] is not None
    with pytest.raises(NotFound):
        await telephony.release(line.exchange, line.org, A_NUMBER, "production")


@postgres
async def test_a_number_moved_between_worlds_moves_between_the_rules_and_never_the_trunk(
    line: Line,
) -> None:
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER, hooked=True)
    )
    trunks = dict(line.server.dialled.trunks)
    moved = await telephony.move(line.exchange, line.org, A_NUMBER, "sandbox")
    assert moved.env == "sandbox"
    assert line.rules() == [f"{line.org}:sandbox"]
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]
    assert line.server.dialled.trunks == trunks


@postgres
async def test_a_whatsapp_number_moves_world_by_its_row_and_nothing_lands_on_the_sfu(
    line: Line,
) -> None:
    wanted = Import(line.corner(), "recepcion", A_NUMBER, channel="whatsapp", hooked=True)
    await telephony.import_number(line.exchange, wanted)
    moved = await telephony.move(line.exchange, line.org, A_NUMBER, "sandbox")
    assert (moved.channel, moved.env) == ("whatsapp", "sandbox")
    assert line.server.dialled.trunks == {}
    assert line.rules() == []


# ── buying on the box's account ──


async def the_box_sells(line: Line, *numbers: str) -> None:
    """The box holds its own Twilio account, which has these numbers for sale."""
    boxs = line.account().model_dump(exclude={"kind", "label"})
    await vault.put_box_credentials(line.exchange.pool, line.exchange.vault, "twilio", boxs)
    line.twilio.for_sale = list(numbers)


@postgres
async def test_a_dry_purchase_names_the_number_it_would_buy_and_buys_nothing(line: Line) -> None:
    await the_box_sells(line, A_NUMBER)
    plan = await telephony.plan_buy(line.exchange, Purchase(line.corner(), "recepcion", "us"))
    assert plan.route.number == A_NUMBER
    assert A_NUMBER in plan.steps[0]
    assert line.twilio.written() == []
    assert line.twilio.for_sale == [A_NUMBER]


@postgres
async def test_a_purchase_buys_attaches_admits_and_routes_the_number_as_the_boxs(
    line: Line,
) -> None:
    await the_box_sells(line, A_NUMBER)
    plan = await telephony.buy_number(
        line.exchange, Purchase(line.corner("sandbox"), "recepcion", "us")
    )
    assert plan.route.managed
    assert A_NUMBER in line.twilio.numbers
    assert line.twilio.numbers[A_NUMBER][1] is not None
    assert list(line.rule("sandbox").numbers) == [A_NUMBER]
    assert await routes.managed_in(line.exchange.pool, line.org, "sandbox") == 1


@postgres
async def test_the_worlds_stock_of_numbers_caps_purchases_before_twilio_is_asked(
    line: Line,
) -> None:
    await the_box_sells(line, A_NUMBER, "+15550100134")
    await orgs.set_quotas(line.exchange.pool, line.org, "production", Quotas(numbers=1))
    await telephony.buy_number(line.exchange, Purchase(line.corner(), "recepcion", "us"))
    asked = len(line.twilio.asked)
    with pytest.raises(QuotaExhausted, match="numbers"):
        await telephony.buy_number(line.exchange, Purchase(line.corner(), "recepcion", "us"))
    assert len(line.twilio.asked) == asked


@postgres
async def test_nothing_for_sale_there_is_not_found_in_our_words(line: Line) -> None:
    await the_box_sells(line)
    with pytest.raises(NotFound, match="no local voice number for sale in US, area code 212"):
        await telephony.buy_number(line.exchange, Purchase(line.corner(), "recepcion", "us", "212"))


@postgres
async def test_a_box_with_no_twilio_of_its_own_buys_for_nobody(line: Line) -> None:
    with pytest.raises(NotAvailable, match="buys no numbers"):
        await telephony.buy_number(line.exchange, Purchase(line.corner(), "recepcion", "us"))


# ── rebuilding the SFU from the tables ──


@postgres
async def test_an_emptied_sfu_gets_every_trunk_and_both_worlds_rules_back(line: Line) -> None:
    await brought(line)
    await telephony.bring(line.exchange, line.org, a_peer())
    line.twilio.owns(A_NUMBER)
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER, account=line.twilio.account_sid)
    )
    await telephony.import_number(
        line.exchange, Import(line.corner("sandbox"), "recepcion", "+15550100134", account="pbx")
    )
    await the_box_sells(line, "+15550100135")
    await telephony.buy_number(line.exchange, Purchase(line.corner(), "recepcion", "us"))
    line.server.dialled.trunks.clear()
    line.server.dialled.rules.clear()
    rebuilt = await telephony.rebuild(line.exchange)
    assert (rebuilt.numbers, rebuilt.refused) == (3, [])
    assert sorted(line.trunk(line.org).numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.trunk(f"{line.org}:pbx").numbers) == ["+15550100134"]
    assert sorted(line.rule("production").numbers) == [A_NUMBER, "+15550100135"]
    assert list(line.rule("sandbox").numbers) == ["+15550100134"]
    asked = len(line.server.dialled.asked)
    await telephony.rebuild(line.exchange)
    written = [
        one
        for one in line.server.dialled.asked[asked:]
        if not isinstance(one, (api.ListSIPInboundTrunkRequest, api.ListSIPDispatchRuleRequest))
    ]
    assert written == []


def test_the_networks_a_twilio_trunk_admits_are_the_ones_the_fence_opens() -> None:
    opened = set(re.findall(r"(\d+\.\d+\.\d+\.\d+/\d+)", NFTABLES.read_text(encoding="utf-8")))
    assert opened == set(telephony.TWILIO_SIGNALLING)


# ── outbound: the account to dial through ──


@postgres
async def test_an_org_with_nothing_is_told_every_step_it_still_owes(line: Line) -> None:
    standing = await telephony.standing(line.exchange, line.corner())
    assert not standing.ready
    assert standing.steps_missing == [telephony.NO_CARRIER]
    await brought(line)
    standing = await telephony.standing(line.exchange, line.corner())
    assert len(standing.steps_missing) == 2
    assert "POST /v1/carrier/outbound" in standing.steps_missing[0]
    assert "no phone number" in standing.steps_missing[1]


@postgres
async def test_provisioning_twilio_is_written_once_and_a_second_run_finds_it_standing(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    plan = await telephony.plan_outbound(line.exchange, line.org)
    assert plan.dry_run
    assert [step.endswith("to do") for step in plan.steps] == [False, True, True, True, True]
    done = await telephony.provision_outbound(line.exchange, line.org)
    (trunk,) = line.twilio.trunks.values()
    assert trunk.domain_name == done.address
    assert str(done.address).endswith(".pstn.twilio.com")
    ((listed, (name, credentials)),) = line.twilio.credential_lists.items()
    assert trunk.credential_lists == [listed]
    assert f"{name}.pstn.twilio.com" == done.address
    assert [username for username, _ in credentials] == [the_orgs_credential(line.org)]
    again = await telephony.provision_outbound(line.exchange, line.org)
    assert all(step.endswith("stands") for step in again.steps)
    assert len(line.twilio.credential_lists) == 1
    assert (await telephony.standing(line.exchange, line.corner())).ready


def the_orgs_credential(org: str) -> str:
    """The username an org dials a Twilio trunk with."""
    return f"pinecall-{org}".replace("_", "-")


@postgres
async def test_two_orgs_on_one_account_dial_one_trunk_each_with_a_credential_of_its_own(
    line: Line,
) -> None:
    other = await orgs.create(line.exchange.pool, "otra", "Otra")
    await brought(line)
    await telephony.bring(line.exchange, other.id, line.account())
    await telephony.provision_outbound(line.exchange, line.org)
    await telephony.provision_outbound(line.exchange, other.id)
    (trunk,) = line.twilio.trunks.values()
    ((listed, (_, credentials)),) = line.twilio.credential_lists.items()
    assert trunk.credential_lists == [listed]
    assert sorted(username for username, _ in credentials) == sorted(
        [the_orgs_credential(line.org), the_orgs_credential(other.id)]
    )


@postgres
async def test_a_credential_whose_password_the_box_lost_is_refused_naming_it(
    line: Line,
) -> None:
    await brought(line)
    host = telephony.termination_host(DOMAIN, line.twilio.account_sid)
    label = host.removesuffix(".pstn.twilio.com")
    lost = [(the_orgs_credential(line.org), "a password nobody here kept")]
    line.twilio.credential_lists[a_sid("CL", 9)] = (label, lost)
    with pytest.raises(Conflict, match="no longer has"):
        await telephony.provision_outbound(line.exchange, line.org)


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
    await telephony.bring(
        line.exchange, line.org, a_peer(outbound_host="sip.pbx.test", outbound_transport="tls")
    )
    done = await telephony.provision_outbound(line.exchange, line.org)
    assert (done.ready, done.address) == (True, "sip.pbx.test")
    await telephony.bring(line.exchange, line.org, a_peer(username="deaf"))
    with pytest.raises(Conflict, match="no outbound_host"):
        await telephony.provision_outbound(line.exchange, line.org, "deaf")


# ── the guards ──


def asking(
    line: Line, to: str = HER_PHONE, env: Env = "production", call: str = "call_1"
) -> Asking:
    """A dial of the org's agent to that number."""
    return Asking(line.corner(env), "recepcion", to, A_NUMBER, "m_ana", call)


@pytest.mark.parametrize(
    ("number", "why"),
    [
        ("0099000001", "E.164"),
        ("+8816000000", "satellite"),
        ("+59899", "shorter"),
        ("+2100000000", "no country calling code"),
    ],
)
def test_a_destination_nobody_could_answer_is_refused_saying_which(number: str, why: str) -> None:
    with pytest.raises(DeclarationRefused, match=why):
        telephony.destination_of(number)


def test_every_calling_code_is_one_two_or_three_digits_and_the_longest_wins() -> None:
    assert all(re.fullmatch(r"\d{1,3}", code) for code in telephony.CALLING_CODES)
    assert telephony.destination_of("+12423000000") == "+12423000000"


def test_the_defaults_are_a_call_back_box_and_a_guard_is_never_negative() -> None:
    guards = telephony.Guards()
    assert (guards.dial_anywhere, guards.per_minute, guards.per_day, guards.max_duration_s) == (
        False,
        6,
        200,
        600,
    )
    with pytest.raises(ValueError, match="per_minute"):
        telephony.Guards(per_minute=-1)


async def ledger(line: Line) -> list[tuple[str, str | None, str | None]]:
    """Every dial written down: who asked, what refused it, the call it became."""
    async with line.exchange.pool.connection() as connection:
        rows = await (
            await connection.execute("select asked_by, refused, call from dials order by id")
        ).fetchall()
    return [(row["asked_by"], row["refused"], row["call"]) for row in rows]


@postgres
async def test_a_number_that_never_called_this_world_is_a_stranger_until_an_operator_lifts_it(
    line: Line,
) -> None:
    with pytest.raises(NotAllowed, match="stranger"):
        await telephony.judged(line.exchange.pool, asking(line))
    await telephony.put_guards(line.exchange.pool, line.org, telephony.Guards(dial_anywhere=True))
    await telephony.judged(line.exchange.pool, asking(line))
    assert await ledger(line) == [("m_ana", "stranger", None), ("m_ana", None, "call_1")]


@postgres
async def test_a_bad_shape_is_written_down_and_refused_with_its_guard(line: Line) -> None:
    with pytest.raises(DeclarationRefused, match=r"\(shape\)"):
        await telephony.judged(line.exchange.pool, asking(line, to="+881600000"))
    assert await ledger(line) == [("m_ana", "shape", None)]


@postgres
async def test_the_pace_counts_the_refusals_too_and_a_days_worth_has_its_own_words(
    line: Line,
) -> None:
    await telephony.put_guards(
        line.exchange.pool,
        line.org,
        telephony.Guards(dial_anywhere=True, per_minute=2, per_day=100),
    )
    with pytest.raises(DeclarationRefused):
        await telephony.judged(line.exchange.pool, asking(line, to="bad"))
    await telephony.judged(line.exchange.pool, asking(line))
    with pytest.raises(QuotaExhausted, match="last minute of its 2 \\(too_fast\\)"):
        await telephony.judged(line.exchange.pool, asking(line))
    await telephony.put_guards(
        line.exchange.pool, line.org, telephony.Guards(dial_anywhere=True, per_minute=50, per_day=3)
    )
    with pytest.raises(QuotaExhausted, match="today of its 3 \\(too_many\\)"):
        await telephony.judged(line.exchange.pool, asking(line))


@postgres
async def test_two_dials_at_once_cannot_both_take_the_last_slot(line: Line) -> None:
    await telephony.put_guards(
        line.exchange.pool, line.org, telephony.Guards(dial_anywhere=True, per_minute=1)
    )
    both = await asyncio.gather(
        telephony.judged(line.exchange.pool, asking(line, call="call_a")),
        telephony.judged(line.exchange.pool, asking(line, call="call_b")),
        return_exceptions=True,
    )
    assert sorted(type(one).__name__ for one in both) == ["Guards", "QuotaExhausted"]


@postgres
async def test_a_second_leg_skips_the_stranger_fence_and_the_calls_own_first_leg_passes(
    line: Line,
) -> None:
    await telephony.judged_second_leg(line.exchange.pool, asking(line, to="+34910000000"))
    await telephony.put_guards(
        line.exchange.pool, line.org, telephony.Guards(dial_anywhere=True, per_minute=2)
    )
    await telephony.judged(line.exchange.pool, asking(line, call="call_2"))
    await telephony.judged_second_leg(line.exchange.pool, asking(line, call="call_2"))
    assert len(await ledger(line)) == 2


# ── placing a call ──


async def ready_to_dial(line: Line) -> None:
    """The org brought its account, imported its number, provisioned it, and dials anyone."""
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await telephony.import_number(line.exchange, Import(line.corner(), "recepcion", A_NUMBER))
    await telephony.provision_outbound(line.exchange, line.org)
    await telephony.put_guards(line.exchange.pool, line.org, telephony.Guards(dial_anywhere=True))


def placing(line: Line, shown: str | None = None) -> Placing:
    """A call back to her phone, asked by Ana."""
    return Placing(line.corner(), "recepcion", HER_PHONE, shown, "m_ana", date(2026, 9, 28))


@postgres
async def test_a_call_placed_opens_its_log_dialing_and_dispatches_its_worlds_fleet(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    logs = Logs(store)
    placed = await telephony.place(line.exchange, logs, placing(line), running=0)
    assert (placed.to, placed.shown) == (HER_PHONE, A_NUMBER)
    (entry,) = await store.whole(placed.call)
    assert entry.type == "call.dialing"
    assert (entry.data["from"], entry.data["to"], entry.data.get("asked_by")) == (
        A_NUMBER,
        HER_PHONE,
        "m_ana",
    )
    (dispatch,) = [one for one in line.server.dispatcher.made if one.room == placed.call]
    assert dispatch.agent_name == "pinecall"
    said = routes.read_dispatch(dispatch.metadata)
    assert said.dial is not None
    assert (said.direction, said.dial.trunk, said.dial.shown) == (
        "outbound",
        line.twilio.account_sid,
        A_NUMBER,
    )
    leg = await telephony.leg_through(line.exchange, asking(line, call=placed.call))
    assert leg.hostname.endswith(".pstn.twilio.com")
    assert leg.username.startswith("pinecall-")


@postgres
async def test_a_number_shown_that_is_not_the_agents_and_an_agent_with_none_are_refused(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    with pytest.raises(DeclarationRefused, match="not a number agent recepcion answers at"):
        await telephony.place(line.exchange, Logs(store), placing(line, "+34910000000"), running=0)
    other = Placing(line.corner(), "agenda", HER_PHONE, None, "m_ana", date(2026, 9, 28))
    with pytest.raises(NotFound, match="answers at no phone number"):
        await telephony.place(line.exchange, Logs(store), other, running=0)


@postgres
async def test_a_number_the_org_hooked_itself_dials_through_nothing(
    line: Line, store: Store
) -> None:
    await telephony.import_number(
        line.exchange, Import(line.corner(), "recepcion", A_NUMBER, hooked=True)
    )
    with pytest.raises(Conflict, match="dials through no account"):
        await telephony.place(line.exchange, Logs(store), placing(line), running=0)


@postgres
async def test_a_dispatch_the_sfu_refuses_ends_the_call_dial_failed_and_seals_it(
    line: Line, store: Store
) -> None:
    await ready_to_dial(line)
    line.server.dispatcher.refusal = api.TwirpError("unavailable", "no fleet", status=503)
    with pytest.raises(UpstreamFailed, match="no fleet"):
        await telephony.place(line.exchange, Logs(store), placing(line), running=0)
    (call,) = [row[2] for row in await ledger(line)]
    assert call is not None
    kinds = [entry.type for entry in await store.whole(call)]
    assert kinds == ["call.dialing", "call.ended"]
    assert await store.sealed(call)


@postgres
async def test_a_whatsapp_number_is_an_account_that_places_no_call(line: Line) -> None:
    await telephony.bring(
        line.exchange,
        line.org,
        WhatsappAccount.model_validate({"phone_number_id": "1055", "access_token": "a token"}),
    )
    standing = await telephony.standing(line.exchange, line.corner())
    assert "places no call" in standing.steps_missing[0]
