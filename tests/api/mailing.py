"""The fixtures about mail: the org's table, the outbox every door hands a letter to, two relays."""

from collections.abc import AsyncIterator, Iterator

import pytest
from cryptography.fernet import Fernet

from pinecall.api.app import app
from pinecall.api.ops.box_settings import the_box_settings
from pinecall.api.org.mail import the_mail, the_outbox
from pinecall.mail import Outbox
from pinecall.orgs.box import BoxSettings, MemoryBoxSettings
from pinecall.orgs.mail import Mail, MemoryMail
from pinecall.types import Mailbox
from tests.api.conftest import A_VAULT_KEY
from tests.mail.fake_smtp import FakeSmtp

# Registered as a plugin by tests/conftest.py, beside tests/api/signing_in.py. The outbox is
# AUTOUSE, unlike everything there, because inviting somebody now hands a letter over: every door
# that mails one asks for it, so the harness always has one and a test that is not about mail
# never mentions it. What it holds by default is a box with no mail server and an empty table,
# which is a gateway that sends nothing and answers exactly as it did before mail existed.

A_BOX_SENDER = "Pinecall <no-reply@box.test>"
AN_ORGS_SENDER = "Clínica Norte <citas@clinica.uy>"


@pytest.fixture
async def relay() -> AsyncIterator[FakeSmtp]:
    """A mail server on the loopback, for a test that wants the BOX to be able to send."""
    async with FakeSmtp() as server:
        yield server


@pytest.fixture
async def the_orgs_relay() -> AsyncIterator[FakeSmtp]:
    """A SECOND mail server, so which one a letter reached is read and never assumed: an org
    that wired its own must never land in the box's."""
    async with FakeSmtp() as server:
        yield server


@pytest.fixture
def the_boxs_mail() -> Mailbox | None:
    """What PINECALL_SMTP_URL would have been read into: nothing, unless a test says otherwise."""
    return None


@pytest.fixture
def mail() -> Mail | None:
    """Where an org's own mail account is kept, empty at the start of every test."""
    return MemoryMail(Fernet(A_VAULT_KEY.encode()))


@pytest.fixture
def box_settings() -> BoxSettings:
    """What the operator configured for the box itself — nothing, at the start of every test."""
    return MemoryBoxSettings(Fernet(A_VAULT_KEY.encode()))


@pytest.fixture(autouse=True)
def outbox(
    the_boxs_mail: Mailbox | None, mail: Mail | None, box_settings: BoxSettings
) -> Iterator[Outbox]:
    """The one place a letter leaves by, over this test's environment, its tables and its box."""
    posting = Outbox(the_boxs_mail, mail, box_settings)
    app.dependency_overrides[the_outbox] = lambda: posting
    app.dependency_overrides[the_mail] = lambda: mail
    app.dependency_overrides[the_box_settings] = lambda: box_settings
    yield posting
    app.dependency_overrides.pop(the_outbox, None)
    app.dependency_overrides.pop(the_mail, None)
    app.dependency_overrides.pop(the_box_settings, None)
