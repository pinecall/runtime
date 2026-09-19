"""The memory store of an agent's tuning: versions, the corner's fallback, and the version gate."""

import pytest

from pinecall.orgs.tuning import MemoryTuning, VersionMoved, tuning_for
from pinecall.types import SANDBOX, Lexicon, Tuning

pytestmark = pytest.mark.unit

ORG = "clinica"
AGENT = "clinica-norte"
ANA = "m_ana"
HAIKU = Tuning(llm="anthropic/claude-haiku-4-5")
SONNET = Tuning(llm="anthropic/claude-sonnet-4-5")


async def put(
    kept: MemoryTuning, holder: str, tuning: Tuning, if_version: int | None = None
) -> int:
    return await kept.put(
        ORG, SANDBOX, holder, AGENT, tuning, author=ANA, note=None, if_version=if_version
    )


async def test_versions_count_from_one_in_every_corner_apart() -> None:
    kept = MemoryTuning()
    assert await put(kept, ANA, HAIKU) == 1
    assert await put(kept, ANA, SONNET, if_version=1) == 2
    assert await put(kept, "", HAIKU) == 1


async def test_a_corner_reads_its_own_newest_and_falls_back_to_the_orgs_own() -> None:
    kept = MemoryTuning()
    assert await kept.newest(ORG, SANDBOX, ANA, AGENT) is None
    await put(kept, "", HAIKU)
    teams = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert teams is not None and (teams.holder, teams.version, teams.value) == ("", 1, HAIKU)
    await put(kept, ANA, SONNET)
    mine = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert mine is not None and (mine.holder, mine.value) == (ANA, SONNET)
    assert await kept.own(ORG, SANDBOX, "m_bruno", AGENT) is None


async def test_a_stale_version_is_refused_with_where_the_corner_is_now() -> None:
    kept = MemoryTuning()
    await put(kept, ANA, HAIKU)
    await put(kept, ANA, SONNET, if_version=1)
    with pytest.raises(VersionMoved) as moved:
        await put(kept, ANA, HAIKU, if_version=1)
    assert moved.value.newest == 2
    with pytest.raises(VersionMoved) as empty:
        await put(kept, "m_bruno", HAIKU, if_version=3)
    assert empty.value.newest == 0


async def test_history_is_newest_first_and_at_reads_one_version_back() -> None:
    kept = MemoryTuning()
    await put(kept, ANA, HAIKU)
    await put(kept, ANA, SONNET)
    assert [row.version for row in await kept.history(ORG, SANDBOX, ANA, AGENT)] == [2, 1]
    first = await kept.at(ORG, SANDBOX, ANA, AGENT, 1)
    assert first is not None and first.value == HAIKU
    assert await kept.at(ORG, SANDBOX, ANA, AGENT, 9) is None


async def test_the_lexicon_is_kept_the_same_way_without_an_agent() -> None:
    kept = MemoryTuning()
    words = Lexicon(said={"GSA": "G S A"}, heard=("Maravilla",))
    assert (
        await kept.put_lexicon(ORG, SANDBOX, "", words, author=ANA, note=None, if_version=None) == 1
    )
    mine = await kept.newest_lexicon(ORG, SANDBOX, ANA)
    assert mine is not None and (mine.holder, mine.value) == ("", words)
    with pytest.raises(VersionMoved):
        await kept.put_lexicon(ORG, SANDBOX, "", words, author=ANA, note=None, if_version=4)


def test_a_gateway_with_no_pool_keeps_its_tuning_in_this_process() -> None:
    assert isinstance(tuning_for(None), MemoryTuning)
