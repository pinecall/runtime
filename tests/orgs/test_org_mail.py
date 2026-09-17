"""The org_mail table: one mailbox per org, replaced whole, sealed at rest, its standing kept."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from pinecall._settings import Settings
from pinecall.log.store import Pool, open_pool
from pinecall.orgs.mail import Mail, MemoryMail, PostgresMail, mail_for
from pinecall.orgs.table import PostgresOrgs
from pinecall.types import Mailbox
from tests.postgres import Dev

SES = Mailbox(
    host="email-smtp.us-east-1.amazonaws.com",
    port=587,
    security="starttls",
    username="AKIAEXAMPLE",
    password="the-derived-smtp-password",
    sender="Clínica Norte <citas@clinica.uy>",
)
RELAY = Mailbox(
    host="127.0.0.1", port=25, security="none", username="", password="", sender="a@clinica.uy"
)
REFUSED = "email-smtp.us-east-1.amazonaws.com:587 refused it — 535 Authentication failed"


async def a_mailbox_kept_rotated_and_forgotten(mail: Mail, org: str) -> None:
    """The one walk both tables take, so the twin and the row cannot tell two stories."""
    assert await mail.of(org) is None
    await mail.put(org, SES)
    kept = await mail.of(org)
    assert kept is not None and kept.mailbox == SES
    assert (kept.verified_at, kept.last_error) == (None, None)
    # A refusal keeps no date it never had; a letter that went through dates the row and clears it.
    await mail.recorded(org, REFUSED)
    refused = await mail.of(org)
    assert refused is not None and (refused.verified_at, refused.last_error) == (None, REFUSED)
    await mail.recorded(org, None)
    worked = await mail.of(org)
    assert worked is not None and worked.verified_at is not None and worked.last_error is None
    # …and a refusal after that keeps the date of the last letter that worked beside it.
    await mail.recorded(org, REFUSED)
    both = await mail.of(org)
    assert both is not None and both.verified_at == worked.verified_at
    # A new wiring is a new account: what a server said about the old password is not news.
    await mail.put(org, RELAY)
    rotated = await mail.of(org)
    assert rotated is not None and rotated.mailbox == RELAY
    assert (rotated.verified_at, rotated.last_error) == (None, None)
    assert await mail.drop(org) is True
    assert await mail.drop(org) is False
    # Recording against an org that wired nothing writes nothing and raises nothing.
    await mail.recorded(org, REFUSED)
    assert await mail.of(org) is None


@pytest.mark.unit
async def test_the_memory_table_keeps_a_mailbox_its_standing_and_nothing_between_orgs() -> None:
    mail = MemoryMail(Fernet(Fernet.generate_key()))
    await a_mailbox_kept_rotated_and_forgotten(mail, "clinica")
    await mail.put("clinica", SES)
    assert await mail.of("tienda") is None


@pytest.mark.unit
def test_no_vault_key_means_no_mail_table_at_all() -> None:
    """The box's own mail needs no vault; an org's password cannot be kept without one."""
    assert mail_for(Settings(vault_key=None), pool=None) is None
    keyed = Settings(vault_key=Fernet.generate_key().decode())
    assert isinstance(mail_for(keyed, pool=None), MemoryMail)


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.postgres
async def test_the_postgres_row_holds_a_token_and_walks_the_way_the_twin_does(pool: Pool) -> None:
    """0034's table, through the very SQL the gateway runs: sealed, replaced, dated, dropped."""
    slug = f"org-{uuid4().hex[:12]}"
    org = await PostgresOrgs(pool).create(slug, slug)
    assert org is not None
    mail = PostgresMail(pool, Fernet(Fernet.generate_key()))
    await mail.put(org.id, SES)
    row = await pool.fetchrow(
        "select host, username, ciphertext from org_mail where org = $1", org.id
    )
    assert row is not None and (row["host"], row["username"]) == (SES.host, SES.username)
    assert SES.password not in str(row["ciphertext"])
    await mail.drop(org.id)
    await a_mailbox_kept_rotated_and_forgotten(mail, org.id)


@pytest.mark.postgres
async def test_removing_the_org_takes_its_mailbox_with_it(pool: Pool) -> None:
    """The foreign key cascades, so `orgs rm` leaves no tenant's SMTP password behind."""
    slug = f"org-{uuid4().hex[:12]}"
    org = await PostgresOrgs(pool).create(slug, slug)
    assert org is not None
    await PostgresMail(pool, Fernet(Fernet.generate_key())).put(org.id, SES)
    await pool.execute("delete from orgs where id = $1", org.id)
    assert await pool.fetchrow("select org from org_mail where org = $1", org.id) is None
