"""Words spent once, kept in Postgres: minted by one gateway, spent by any, once."""

from cryptography.fernet import Fernet
from pydantic import TypeAdapter

from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy.words import OneUse, Words
from tests.conftest import postgres

VAULT = vault_of(Fernet.generate_key().decode())
TEXT: TypeAdapter[str] = TypeAdapter(str)


def a_kind(pool: Pool, now: list[float]) -> OneUse[str]:
    return OneUse(("w_", 60.0), TEXT, Words(pool, VAULT, lambda: now[0]))


@postgres
async def test_a_word_minted_by_one_is_spent_by_another_once(pool: Pool) -> None:
    now = [100.0]
    minted, spending = a_kind(pool, now), a_kind(pool, now)
    word, dies = await minted.mint("the key")
    assert (word.startswith("w_"), dies) == (True, 160.0)
    assert await spending.spend(word) == "the key"
    assert await minted.spend(word) is None


@postgres
async def test_a_word_dies_on_its_own_and_is_read_dead_but_never_spent(pool: Pool) -> None:
    now = [100.0]
    kind = a_kind(pool, now)
    word, _ = await kind.mint("v")
    now[0] = 161.0
    dead = await kind.read(word)
    assert dead is not None
    assert (dead.value, dead.expires_at) == ("v", 160.0)
    assert await kind.alive(word) is None
    assert await kind.spend(word) is None


@postgres
async def test_a_living_word_is_filled_its_end_kept_and_its_wrong_guesses_counted(
    pool: Pool,
) -> None:
    now = [0.0]
    kind = a_kind(pool, now)
    word, dies = await kind.mint("waiting")
    assert await kind.fill(word, "answered")
    assert [await kind.tried(word) for _ in range(2)] == [1, 2]
    kept = await kind.alive(word)
    assert kept is not None
    assert (kept.value, kept.expires_at, kept.attempts) == ("answered", dies, 2)
    await kind.forget(word)
    assert await kind.read(word) is None
    assert not await kind.fill(word, "late")
