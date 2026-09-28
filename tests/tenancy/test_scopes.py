"""Corners: versions per scope, knob by knob from the nearest that sets it, read in one query."""

import asyncio

import pytest

from pinecall.domain.agent import Docs, Greeting, Lexicon, Tuning, Versions
from pinecall.domain.errors import Conflict
from pinecall.domain.org import Org
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from pinecall.tenancy.scopes import (
    Written,
    current,
    every_tuning,
    lexicon_at,
    lexicon_history,
    lexicon_side_by_side,
    put_lexicon,
    put_tuning,
    tuning_at,
    tuning_history,
    tuning_side_by_side,
)
from tests.conftest import postgres

AGENT = "recepcion"
BY_ANA = Written(author="m_ana")


async def _corners(pool: Pool) -> tuple[Scope, Scope, Org]:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    return Scope(org.id, "sandbox", "m_ana"), Scope(org.id, "sandbox", ""), org


@postgres
async def test_versions_count_from_one_and_a_corner_falls_back_to_the_orgs_own(
    pool: Pool,
) -> None:
    mine, team, _ = await _corners(pool)
    assert await put_tuning(pool, team, AGENT, Tuning(voice="team-voice"), BY_ANA) == 1
    assert (await current(pool, mine, AGENT)).tuning.voice == "team-voice"
    assert await put_tuning(pool, mine, AGENT, Tuning(voice="mine"), BY_ANA) == 1
    assert await put_tuning(pool, mine, AGENT, Tuning(voice="mine-again"), BY_ANA) == 2
    assert (await current(pool, mine, AGENT)).tuning.voice == "mine-again"


@postgres
async def test_every_knob_falls_through_on_its_own_and_an_empty_row_supplies_nothing(
    pool: Pool,
) -> None:
    mine, team, _ = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(voice="v", llm="acme/acme-1"), BY_ANA)
    await put_tuning(pool, mine, AGENT, Tuning(llm="acme/acme-2"), BY_ANA)
    await put_tuning(pool, mine, AGENT, Tuning(), BY_ANA)
    is_standing = await current(pool, mine, AGENT)
    assert (is_standing.tuning.voice, is_standing.tuning.llm) == ("v", "acme/acme-1")
    assert is_standing.versions.config == 1


@postgres
async def test_a_knob_set_to_a_falsy_value_wins_over_the_corner_below(pool: Pool) -> None:
    mine, team, _ = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(record=True, max_duration_s=600), BY_ANA)
    await put_tuning(pool, mine, AGENT, Tuning(record=False, max_duration_s=0), BY_ANA)
    is_standing = await current(pool, mine, AGENT)
    assert (is_standing.tuning.record, is_standing.tuning.max_duration_s) == (False, 0)


@postgres
async def test_no_base_at_all_is_a_decision_and_the_teams_bases_are_not_heard(pool: Pool) -> None:
    mine, team, _ = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(bases=(Docs(base="faq"),)), BY_ANA)
    assert (await current(pool, mine, AGENT)).tuning.bases == (Docs(base="faq"),)
    await put_tuning(pool, mine, AGENT, Tuning(bases=()), BY_ANA)
    assert (await current(pool, mine, AGENT)).tuning.bases == ()


@postgres
async def test_every_knob_survives_the_column_whole(pool: Pool) -> None:
    mine, _, _ = await _corners(pool)
    whole = Tuning(
        voice="v",
        tts="acme",
        tts_model="acme-voice-2",
        stt="acme/ears",
        llm="acme/acme-1",
        greeting=Greeting(say="Hola", allow_interruptions=False),
        record=True,
        max_duration_s=300,
        knowledge="# Horarios",
        bases=(Docs(base="faq", mode="tool", k=3, min_score=0.1),),
    )
    await put_tuning(pool, mine, AGENT, whole, BY_ANA)
    assert (await current(pool, mine, AGENT)).tuning == whole


@postgres
async def test_a_stale_version_is_refused_with_where_the_corner_is_now(pool: Pool) -> None:
    mine, _, _ = await _corners(pool)
    await put_tuning(pool, mine, AGENT, Tuning(voice="a"), BY_ANA)
    await put_tuning(pool, mine, AGENT, Tuning(voice="b"), Written("m_ana", if_version=1))
    with pytest.raises(Conflict, match="at v2 now"):
        await put_tuning(pool, mine, AGENT, Tuning(voice="c"), Written("m_ana", if_version=1))


@postgres
async def test_two_writers_that_read_the_same_version_do_not_both_win(pool: Pool) -> None:
    mine, _, _ = await _corners(pool)
    both = await asyncio.gather(
        put_tuning(pool, mine, AGENT, Tuning(voice="a"), Written("m_ana", if_version=0)),
        put_tuning(pool, mine, AGENT, Tuning(voice="b"), Written("m_bo", if_version=0)),
        return_exceptions=True,
    )
    assert sorted(type(outcome).__name__ for outcome in both) == ["Conflict", "int"]


@postgres
async def test_history_is_the_corners_own_newest_first_and_at_reads_one_back(pool: Pool) -> None:
    mine, team, _ = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(voice="team"), BY_ANA)
    await put_tuning(pool, mine, AGENT, Tuning(voice="a"), Written("m_ana", note="first"))
    await put_tuning(pool, mine, AGENT, Tuning(voice="b"), BY_ANA)
    history = await tuning_history(pool, mine, AGENT)
    assert [(kept.version, kept.value.voice) for kept in history] == [(2, "b"), (1, "a")]
    assert history[1].note == "first"
    at = await tuning_at(pool, mine, AGENT, 1)
    assert at is not None
    assert (at.holder, at.value.voice) == ("m_ana", "a")
    assert await tuning_at(pool, mine, AGENT, 9) is None


@postgres
async def test_the_lexicon_is_kept_the_same_way_without_an_agent(pool: Pool) -> None:
    mine, team, _ = await _corners(pool)
    words = Lexicon(said={"Pinecall": "páin col"}, heard=("Pinecall",))
    await put_lexicon(pool, team, words, BY_ANA)
    assert (await current(pool, mine, AGENT)).lexicon == words
    await put_lexicon(pool, mine, Lexicon(heard=("turno",)), BY_ANA)
    is_standing = await current(pool, mine, "any-agent")
    assert is_standing.lexicon == Lexicon(heard=("turno",))
    assert is_standing.versions == Versions(config=None, lexicon=1)
    assert [kept.version for kept in await lexicon_history(pool, mine)] == [1]
    kept = await lexicon_at(pool, mine, 1)
    assert kept is not None
    assert kept.value.heard == ("turno",)


@postgres
async def test_an_unset_corner_stands_on_nothing(pool: Pool) -> None:
    mine, _, _ = await _corners(pool)
    assert await current(pool, mine, AGENT) == (await current(pool, mine, AGENT))
    is_standing = await current(pool, mine, AGENT)
    assert (is_standing.tuning, is_standing.lexicon, is_standing.versions) == (
        Tuning(),
        Lexicon(),
        Versions(),
    )


@postgres
async def test_each_corner_is_its_own_newest_and_never_the_fallback(pool: Pool) -> None:
    mine, team, org = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(voice="team"), BY_ANA)
    await put_tuning(pool, Scope(org.id, "production"), AGENT, Tuning(voice="live"), BY_ANA)
    shown = await tuning_side_by_side(pool, mine, AGENT)
    assert shown.yours is None
    assert shown.team is not None
    assert shown.production is not None
    assert (shown.team.value.voice, shown.production.value.voice) == ("team", "live")
    await put_lexicon(pool, mine, Lexicon(heard=("mío",)), BY_ANA)
    words = await lexicon_side_by_side(pool, mine)
    assert words.yours is not None
    assert words.yours.value.heard == ("mío",)
    assert (words.team, words.production) == (None, None)


@postgres
async def test_a_key_holding_no_agent_has_no_corner_of_its_own(pool: Pool) -> None:
    _, team, _ = await _corners(pool)
    await put_tuning(pool, team, AGENT, Tuning(voice="team"), BY_ANA)
    assert (await tuning_side_by_side(pool, team, AGENT)).yours is None


@postgres
async def test_every_agents_tuning_stands_in_the_corner_as_each_one_would(pool: Pool) -> None:
    mine, team, _ = await _corners(pool)
    await put_tuning(pool, team, "recepcion", Tuning(voice="team"), BY_ANA)
    await put_tuning(pool, mine, "turnos", Tuning(llm="acme/acme-1"), BY_ANA)
    assert await every_tuning(pool, mine) == {
        "recepcion": Tuning(voice="team"),
        "turnos": Tuning(llm="acme/acme-1"),
    }
