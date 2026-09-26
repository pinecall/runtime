"""A person's key: for good at production, a day on a sandbox, never longer than its parent."""

from datetime import UTC, datetime, timedelta

import pytest

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.person_keys import SANDBOX_PERSONS_KEY_LIFE, a_persons_key, until
from pinecall.types import PRODUCTION, SANDBOX, Member

pytestmark = pytest.mark.unit

BERNA = Member(
    id="m_berna", org="clinica", email="berna@clinica.uy", name="Berna", role="developer"
)


async def test_a_persons_key_at_production_never_expires() -> None:
    issued = await a_persons_key(MemoryKeys(), BERNA, "laptop", PRODUCTION)
    assert issued.record.expires_at is None
    assert (issued.record.subject, issued.record.env) == (BERNA.id, SANDBOX)


async def test_a_persons_key_on_a_sandbox_lives_a_day() -> None:
    """A member production disables later loses the sandbox within that day."""
    before = datetime.now(UTC)
    issued = await a_persons_key(MemoryKeys(), BERNA, "console", SANDBOX)
    assert issued.record.expires_at is not None
    assert before + SANDBOX_PERSONS_KEY_LIFE <= issued.record.expires_at
    assert issued.record.expires_at <= datetime.now(UTC) + SANDBOX_PERSONS_KEY_LIFE


def test_a_key_minted_from_a_key_never_outlives_the_one_it_came_from() -> None:
    """Otherwise the next key, minted before the last one died, would keep the day from coming."""
    soon = datetime.now(UTC) + timedelta(hours=1)
    parent = KeyRecord(key_id="k_1", org="clinica", subject=BERNA.id, expires_at=soon)
    assert until(SANDBOX, parent) == soon
    assert until(PRODUCTION, parent) is None
