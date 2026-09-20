"""An agent's tuning in Postgres: a row a version, the corner's fall-through, the version race."""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.log.store import Pool, open_pool
from pinecall.orgs.table import PostgresOrgs
from pinecall.orgs.tuning import PostgresTuning, VersionMoved
from pinecall.types import (
    PRODUCTION,
    SANDBOX,
    Docs,
    Greeting,
    Hangup,
    Lexicon,
    MemoryPolicy,
    Tuning,
    Turn,
)
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

ANA = "m_ana"
HAIKU = Tuning(llm="anthropic/claude-haiku-4-5")
SONNET = Tuning(llm="anthropic/claude-sonnet-4-5")
WHOLE = Tuning(
    voice="carolina",
    tts="elevenlabs",
    llm="anthropic/claude-haiku-4-5",
    greeting=Greeting(say="Buenas.", allow_interruptions=False),
    hangup=Hangup(when="the caller says bye"),
    turn=Turn(min_interruption_words=2, endpointing_ms=300),
    memory=MemoryPolicy(remember=("allergies",), forget=("card numbers",)),
    knowledge="# Clínica Norte\n\nAbrimos a las nueve.",
    bases=(Docs(base="clinica", k=4), Docs(base="precios", mode="tool")),
)


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """A tenant of this test's own: the rows are one org's and outlive a test."""
    slug = f"org-{uuid4().hex[:12]}"
    created = await PostgresOrgs(pool).create(slug, slug)
    assert created is not None
    return created.id


@pytest.fixture
def agent() -> str:
    return f"clinica-{uuid4().hex[:8]}"


async def put(
    kept: PostgresTuning,
    org: str,
    holder: str,
    agent: str,
    tuning: Tuning,
    if_version: int | None = None,
) -> int:
    return await kept.put(
        org, SANDBOX, holder, agent, tuning, author=ANA, note=None, if_version=if_version
    )


async def test_versions_count_from_one_and_a_corner_falls_back_to_the_orgs_own(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    assert await kept.newest(org, SANDBOX, ANA, agent) is None
    assert await put(kept, org, "", agent, HAIKU) == 1
    teams = await kept.newest(org, SANDBOX, ANA, agent)
    assert teams is not None and (teams.holder, teams.version, teams.value) == ("", 1, HAIKU)
    assert await put(kept, org, ANA, agent, SONNET) == 1
    mine = await kept.newest(org, SANDBOX, ANA, agent)
    assert mine is not None and (mine.holder, mine.version, mine.value) == (ANA, 1, SONNET)
    assert await kept.newest(org, PRODUCTION, None, agent) is None


# Berna's worked example, over the column: the team runs a model and an ear, Ana has set only a
# voice, and every knob she did not set is still the team's.
async def test_every_knob_falls_through_on_its_own_and_an_empty_row_supplies_nothing(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, Tuning(llm=SONNET.llm, stt="soniox"))
    await put(kept, org, ANA, agent, Tuning(voice="carolina"))
    read = await kept.newest(org, SANDBOX, ANA, agent)
    assert read is not None and (read.holder, read.version) == (ANA, 1)
    assert read.value == Tuning(voice="carolina", llm=SONNET.llm, stt="soniox")
    # What `clear` leaves behind is a version of its own that blanks nothing under it.
    assert await put(kept, org, ANA, agent, Tuning(), if_version=1) == 2
    cleared = await kept.newest(org, SANDBOX, ANA, agent)
    assert cleared is not None and (cleared.holder, cleared.version) == ("", 1)
    assert cleared.value == Tuning(llm=SONNET.llm, stt="soniox")
    # And every agent at once, which the knowledge screen reads, falls through the same way.
    assert (await kept.every_newest(org, SANDBOX, ANA))[agent].value == cleared.value


# The one knob that had no absent form: an empty `bases` was dropped on its way into the column,
# so "I read no base" and "I never attached one" were the same row and both fell through.
async def test_no_base_at_all_is_kept_in_the_column_and_wins_over_the_teams_bases(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, Tuning(bases=(Docs(base="clinica", k=4),), llm=SONNET.llm))
    await put(kept, org, ANA, agent, Tuning(bases=()))
    read = await kept.newest(org, SANDBOX, ANA, agent)
    assert read is not None and (read.holder, read.version) == (ANA, 1)
    assert read.value == Tuning(bases=(), llm=SONNET.llm)
    # And the corner's own row says it too, read back off the column as it was written.
    own = await kept.own(org, SANDBOX, ANA, agent)
    assert own is not None and own.value.bases == ()


async def test_bases_nobody_attached_fall_through_to_the_orgs_own(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, Tuning(bases=(Docs(base="clinica", k=4),)))
    await put(kept, org, ANA, agent, Tuning(voice="carolina"))
    read = await kept.newest(org, SANDBOX, ANA, agent)
    assert read is not None
    assert read.value == Tuning(voice="carolina", bases=(Docs(base="clinica", k=4),))


async def test_every_knob_survives_the_column_whole(pool: Pool, org: str, agent: str) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, WHOLE)
    read = await kept.newest(org, SANDBOX, None, agent)
    assert read is not None and read.value == WHOLE
    assert read.author == ANA and read.set_at is not None


async def test_a_stale_version_is_refused_with_where_the_corner_is_now(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, ANA, agent, HAIKU)
    await put(kept, org, ANA, agent, SONNET, if_version=1)
    with pytest.raises(VersionMoved) as moved:
        await put(kept, org, ANA, agent, HAIKU, if_version=1)
    assert moved.value.newest == 2


# Two people saving from two screens: both read v1, both compute v2, and the primary key lets
# exactly one of them in. The other is told, and never quietly written over the first.
async def test_two_writers_that_read_the_same_version_do_not_both_win(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, HAIKU)
    outcomes = await asyncio.gather(
        put(kept, org, "", agent, SONNET, if_version=1),
        put(kept, org, "", agent, WHOLE, if_version=1),
        return_exceptions=True,
    )
    landed = [one for one in outcomes if isinstance(one, int)]
    refused = [one for one in outcomes if isinstance(one, VersionMoved)]
    assert (landed, [one.newest for one in refused]) == ([2], [2])


async def test_history_is_the_corners_own_newest_first_and_at_reads_one_back(
    pool: Pool, org: str, agent: str
) -> None:
    kept = PostgresTuning(pool)
    await put(kept, org, "", agent, HAIKU)
    await put(kept, org, "", agent, SONNET)
    await put(kept, org, ANA, agent, WHOLE)
    assert [row.version for row in await kept.history(org, SANDBOX, "", agent)] == [2, 1]
    assert [row.version for row in await kept.history(org, SANDBOX, ANA, agent)] == [1]
    # A version the corner has not, read through the fallback: the org's own v2.
    fell_back = await kept.at(org, SANDBOX, ANA, agent, 2)
    assert fell_back is not None and (fell_back.holder, fell_back.value) == ("", SONNET)


async def test_the_lexicon_is_kept_the_same_way_without_an_agent(pool: Pool, org: str) -> None:
    kept = PostgresTuning(pool)
    words = Lexicon(said={"GSA": "G S A", "Vidal": "bidál"}, heard=("Maravilla", "Naples"))
    assert (
        await kept.put_lexicon(org, SANDBOX, "", words, author=ANA, note=None, if_version=None) == 1
    )
    read = await kept.newest_lexicon(org, SANDBOX, ANA)
    assert read is not None and (read.holder, read.version, read.value) == ("", 1, words)
    with pytest.raises(VersionMoved) as moved:
        await kept.put_lexicon(org, SANDBOX, "", words, author=ANA, note=None, if_version=7)
    assert moved.value.newest == 1
    assert [row.version for row in await kept.lexicon_history(org, SANDBOX, "")] == [1]
    at = await kept.lexicon_at(org, SANDBOX, ANA, 1)
    assert at is not None and at.value == words
