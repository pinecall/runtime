"""Tests for an org's carrier accounts: kept sealed, many per org, one named, dropped."""

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import Conflict, NotAvailable, NotFound
from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy import carriers, orgs
from pinecall.tenancy.carriers import SipPeer, Termination, TwilioAccount
from tests.conftest import postgres
from tests.fakes.idp import a_sid

SEALED = vault_of(Fernet.generate_key().decode())
TWILIO = TwilioAccount.model_validate(
    {"account_sid": a_sid("AC", 1), "user": a_sid("SK", 2), "secret": "the secret"}
)
PEER = SipPeer.model_validate(
    {"username": "pbx", "password": "a peer's password", "addresses": ["203.0.113.0/24"]}
)


def test_a_sid_that_is_not_one_is_refused() -> None:
    with pytest.raises(ValueError, match="account_sid"):
        TwilioAccount.model_validate(
            {"account_sid": "AC123", "user": a_sid("SK", 2), "secret": "s"}
        )


def test_a_peer_needs_its_networks_and_a_kind_nobody_knows_is_refused() -> None:
    with pytest.raises(ValueError, match="addresses"):
        SipPeer.model_validate({"username": "pbx", "password": "p", "addresses": []})
    with pytest.raises(ValueError, match="not a network"):
        SipPeer.model_validate({"username": "pbx", "password": "p", "addresses": ["the office"]})
    with pytest.raises(ValueError, match="kind"):
        carriers.ACCOUNT.validate_python({"kind": "telegraph"})


def test_a_peer_dialled_with_half_a_pair_is_refused() -> None:
    with pytest.raises(ValueError, match="outbound_password"):
        SipPeer.model_validate(
            {
                "username": "pbx",
                "password": "p",
                "addresses": ["203.0.113.0/24"],
                "outbound_username": "out",
            }
        )


@postgres
async def test_an_account_is_kept_sealed_and_never_its_secret_in_the_row(pool: Pool) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    await carriers.put_carrier(pool, SEALED, org, TWILIO)
    async with pool.connection() as connection:
        row = await (await connection.execute("select * from carriers")).fetchone()
    assert row is not None
    assert (row["kind"], row["account"]) == ("twilio", TWILIO.account_sid)
    assert "the secret" not in str(row["ciphertext"])
    assert (await carriers.carrier_named(pool, SEALED, org)).account == TWILIO


@postgres
async def test_an_org_holds_many_accounts_and_names_one_when_there_are_several(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    await carriers.put_carrier(pool, SEALED, org, TWILIO)
    await carriers.put_carrier(pool, SEALED, org, PEER)
    listed = await carriers.carriers_of(pool, SEALED, org)
    assert [carrier.id for carrier in listed] == [TWILIO.account_sid, "pbx"]
    with pytest.raises(Conflict, match="2 carrier accounts"):
        await carriers.carrier_named(pool, SEALED, org)
    assert isinstance((await carriers.carrier_named(pool, SEALED, org, "pbx")).account, SipPeer)


@postgres
async def test_bringing_an_account_again_replaces_its_secret_and_keeps_its_termination(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    kept = await carriers.put_carrier(pool, SEALED, org, TWILIO)
    termination = Termination.model_validate(
        {"host": "h.pstn.twilio.com", "username": "u", "password": "p"}
    )
    await carriers.seal_carrier(pool, SEALED, carriers.Carrier(kept.org, kept.account, termination))
    again = TWILIO.model_copy(update={"secret": "a new secret"})
    await carriers.put_carrier(pool, SEALED, org, again)
    carrier = await carriers.carrier_named(pool, SEALED, org)
    assert (carrier.account, carrier.outbound) == (again, termination)


@postgres
async def test_an_account_dropped_is_gone_and_a_sealed_row_under_a_lost_key_is_skipped(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    await carriers.put_carrier(pool, SEALED, org, TWILIO)
    other = vault_of(Fernet.generate_key().decode())
    await carriers.put_carrier(pool, other, org, PEER)
    assert [carrier.id for carrier in await carriers.carriers_of(pool, SEALED, org)] == [
        TWILIO.account_sid
    ]
    assert await carriers.drop_carrier(pool, SEALED, org, TWILIO.account_sid) == TWILIO.account_sid
    with pytest.raises(NotFound, match="no carrier account"):
        await carriers.carrier_named(pool, SEALED, org)


@postgres
async def test_a_box_with_no_twilio_of_its_own_says_so(pool: Pool) -> None:
    with pytest.raises(NotAvailable, match="buys no numbers"):
        await carriers.box_twilio(pool, SEALED)
