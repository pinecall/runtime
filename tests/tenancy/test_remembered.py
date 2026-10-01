"""Tests for the keys a gateway remembers: seconds from memory, forgotten on any revocation."""

import asyncio
from dataclasses import dataclass

from pinecall.domain.person import Key
from pinecall.postgres.pool import Pool
from pinecall.process.signal import LocalSignal
from pinecall.tenancy.keys import Bearer, Issued, issue, revoke
from pinecall.tenancy.people import fingerprint
from pinecall.tenancy.remembered import Remembered, RememberedKeys
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

pytestmark = postgres


@dataclass
class Ticking:
    """A clock the test moves."""

    now: float = 1000.0

    def __call__(self) -> float:
        """The reading."""
        return self.now


async def remembering(pool: Pool, signal: LocalSignal, clock: Ticking) -> RememberedKeys:
    """A gateway's memory of keys, listening on the signal."""
    remembered = RememberedKeys(pool, signal, clock=clock)
    await remembered.start()
    await asyncio.sleep(0)
    return remembered


async def test_a_key_revoked_behind_the_gateways_back_opens_for_the_seconds_and_no_longer(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="sandbox"))
    clock = Ticking()
    remembered = await remembering(pool, LocalSignal(), clock)
    verified = await remembered.verify(secret)
    assert verified is not None
    assert verified.key.org == org.id
    await revoke(pool, fingerprint(secret))
    assert await remembered.verify(secret) is not None, "remembered: the database was not asked"
    clock.now += 5.0
    assert await remembered.verify(secret) is None
    assert fingerprint(secret) not in remembered.kept
    await remembered.close()


async def test_a_revocation_said_by_one_gateway_forgets_the_key_on_every_other_at_once(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    signal = LocalSignal()
    clock = Ticking()
    first = await remembering(pool, signal, clock)
    second = await remembering(pool, signal, clock)
    assert await first.verify(secret) is not None
    assert await second.verify(secret) is not None
    await revoke(pool, fingerprint(secret))
    first.forget(fingerprint=fingerprint(secret))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fingerprint(secret) not in first.kept
    assert fingerprint(secret) not in second.kept
    assert await second.verify(secret) is None
    await first.close()
    await second.close()


async def test_a_persons_revocation_forgets_every_key_of_theirs_and_nobody_elses(
    pool: Pool,
) -> None:
    signal = LocalSignal()
    clock = Ticking()
    remembered = await remembering(pool, signal, clock)
    other = await remembering(pool, signal, clock)
    for memory in (remembered, other):
        memory.kept["h1"] = Remembered(Bearer(Key(key_id="k1", org="o", subject="m_1")), 2000.0)
        memory.kept["h2"] = Remembered(Bearer(Key(key_id="k2", org="o", subject="m_1")), 2000.0)
        memory.kept["h3"] = Remembered(Bearer(Key(key_id="k3", org="o", subject="m_2")), 2000.0)
    remembered.forget(subject="m_1")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert set(remembered.kept) == {"h3"}
    assert set(other.kept) == {"h3"}
    await remembered.close()
    await other.close()
