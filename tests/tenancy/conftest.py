"""What the tenancy tests share: an org to write into, and a persona to write."""

import smtplib

import pytest

from pinecall.domain.org import Org
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from pinecall.tenancy.personas import Persona
from tests.fakes.mail import MailServer, Postbox

MARTA = Persona(
    name="marta",
    goal="move her appointment to Friday",
    style="brief, a little impatient",
    facts={"dni": "12345678Z"},
    state={"patient": {"name": "Marta"}},
    llm="acme/acme-1",
    accepts_when="a Friday slot",
)


async def an_org(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


@pytest.fixture
def postbox(monkeypatch: pytest.MonkeyPatch) -> Postbox:
    kept = Postbox()
    monkeypatch.setattr(MailServer, "postbox", kept)
    monkeypatch.setattr(smtplib, "SMTP", MailServer)
    monkeypatch.setattr(smtplib, "SMTP_SSL", MailServer)
    return kept
