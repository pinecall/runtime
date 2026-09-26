"""The memory store of an agent's tuning: versions, the corner's fall-through, the version gate."""

import pytest

from pinecall.orgs.tuning_store import VersionMoved, tuning_for
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.types import SANDBOX, Docs, Hangup, Lexicon, Tuning, Turn

pytestmark = pytest.mark.unit

ORG = "clinica"
AGENT = "clinica-norte"
ANA = "m_ana"
HAIKU = Tuning(llm="anthropic/claude-haiku-4-5")
SONNET = Tuning(llm="anthropic/claude-sonnet-4-5")
# The team runs a model and an ear; Ana, in her own corner, has only picked a voice.
THE_TEAMS = Tuning(llm="anthropic/claude-sonnet-4-5", stt="soniox")
A_VOICE = Tuning(voice="nova")


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


async def test_every_knob_falls_through_on_its_own_and_a_voice_keeps_the_teams_model() -> None:
    """Ana picks a voice and goes on hearing the team's llm and stt: nothing of theirs is unset."""
    kept = MemoryTuning()
    await put(kept, "", THE_TEAMS)
    await put(kept, ANA, A_VOICE)
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None
    assert read.value == Tuning(voice="nova", llm=SONNET.llm, stt="soniox")
    # Whose corner and which version: the nearest one that supplied a knob, which is Ana's own.
    assert (read.holder, read.version) == (ANA, 1)


async def test_an_empty_row_supplies_nothing_and_the_corner_below_is_heard_whole() -> None:
    """What `clear` leaves behind: a version of its own, and not one knob blanked under it."""
    kept = MemoryTuning()
    await put(kept, "", THE_TEAMS)
    await put(kept, ANA, A_VOICE)
    assert await put(kept, ANA, Tuning(), if_version=1) == 2
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None and read.value == THE_TEAMS
    assert (read.holder, read.version) == ("", 1)
    # An empty row over nothing at all is nothing at all: the runtime's own defaults stand.
    assert await kept.newest(ORG, SANDBOX, "m_bruno", "nobody-tuned-this") is None


async def test_a_knob_set_to_a_falsy_value_wins_over_the_corner_below() -> None:
    """Zero is a decision: only an absent knob falls through, never one somebody set to nothing."""
    kept = MemoryTuning()
    await put(kept, "", Tuning(turn=Turn(endpointing_ms=700), hangup=Hangup(when="at goodbye")))
    await put(kept, ANA, Tuning(turn=Turn(endpointing_ms=0)))
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None
    assert read.value == Tuning(turn=Turn(endpointing_ms=0), hangup=Hangup(when="at goodbye"))


# The same rule, on the one knob where getting it wrong would be silent: `record=False` is an org
# saying it does not keep audio, and it has to win over a corner that does. A `bool` with a default
# — rather than `bool | None` — would be dropped by as_json as if nobody had said anything.
async def test_an_agent_told_not_to_record_is_not_a_corner_that_never_said() -> None:
    kept = MemoryTuning()
    await put(kept, "", Tuning(record=True))
    await put(kept, ANA, Tuning(record=False))
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None
    assert read.value.record is False


async def test_no_base_at_all_is_a_decision_and_the_teams_bases_are_not_heard() -> None:
    """`bases []` in your corner is "I read none": it used to be dropped and fall through."""
    kept = MemoryTuning()
    await put(kept, "", Tuning(bases=(Docs(base="clinica"),), llm=SONNET.llm))
    await put(kept, ANA, Tuning(bases=()))
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None and read.value == Tuning(bases=(), llm=SONNET.llm)
    assert (read.holder, read.version) == (ANA, 1)


async def test_bases_nobody_attached_fall_through_to_the_corner_below() -> None:
    """The other half of it: absent is absent, and the team's bases are read."""
    kept = MemoryTuning()
    await put(kept, "", Tuning(bases=(Docs(base="clinica"),)))
    await put(kept, ANA, A_VOICE)
    read = await kept.newest(ORG, SANDBOX, ANA, AGENT)
    assert read is not None
    assert read.value == Tuning(voice="nova", bases=(Docs(base="clinica"),))


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
    words = Lexicon(said={"GSA": "G S A"}, heard=("Clínica Norte",))
    assert (
        await kept.put_lexicon(ORG, SANDBOX, "", words, author=ANA, note=None, if_version=None) == 1
    )
    mine = await kept.newest_lexicon(ORG, SANDBOX, ANA)
    assert mine is not None and (mine.holder, mine.value) == ("", words)
    with pytest.raises(VersionMoved):
        await kept.put_lexicon(ORG, SANDBOX, "", words, author=ANA, note=None, if_version=4)


def test_a_gateway_with_no_pool_keeps_its_tuning_in_this_process() -> None:
    assert isinstance(tuning_for(None), MemoryTuning)
