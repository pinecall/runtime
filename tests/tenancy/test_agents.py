"""Per agent: its widget's look and hold melody per world, the org's personas, its caller codes."""

import asyncio

import pytest

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound, QuotaExhausted
from pinecall.domain.types import Corner, Org
from pinecall.log.log import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy.agents import (
    CLAIMED,
    ISSUED,
    LIVE_PER_AGENT,
    Chosen,
    Clip,
    Codes,
    Look,
    Persona,
    drop_persona,
    forget_hold,
    hold_audio,
    hold_of,
    keep_hold,
    look_of,
    persona,
    personas_of,
    put_look,
    put_persona,
    silence_hold,
)
from pinecall.tenancy.orgs import create
from pinecall.wire.frames import Entry
from tests.conftest import postgres

AGENT = "recepcion"
MARTA = Persona(
    name="marta",
    goal="move her appointment to Friday",
    style="brief, a little impatient",
    facts={"dni": "12345678Z"},
    state={"patient": {"name": "Marta"}},
    llm="acme/acme-1",
    accepts_when="a Friday slot",
)


async def _org(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


@postgres
async def test_a_widget_round_trips_per_world_and_one_nobody_set_is_the_default(
    pool: Pool,
) -> None:
    org = await _org(pool)
    sandbox, production = Corner(org.id, "sandbox"), Corner(org.id, "production")
    look = Look(title="Clínica", greeting="Hola", accent="#cd58b2", autostart=True, theme="dark")
    await put_look(pool, sandbox, AGENT, look)
    assert await look_of(pool, sandbox, AGENT) == look
    assert await look_of(pool, production, AGENT) == Look()


def test_a_widget_too_long_or_with_an_accent_that_is_no_colour_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="80 characters at most"):
        Look(title="x" * 81)
    with pytest.raises(DeclarationRefused, match="a CSS colour"):
        Look(accent="red; background: url(x)")
    assert Look(accent="rgb(205 88 178)").accent == "rgb(205 88 178)"


@postgres
async def test_an_agent_plays_the_boxs_melody_until_it_says_otherwise_in_its_world(
    pool: Pool,
) -> None:
    org = await _org(pool)
    sandbox, production = Corner(org.id, "sandbox"), Corner(org.id, "production")
    assert await hold_of(pool, sandbox, AGENT) is None
    chosen = await keep_hold(pool, sandbox, AGENT, Clip(b"OggS...", 12.5, "jingle.ogg"))
    assert chosen.played == "custom"
    assert await hold_of(pool, sandbox, AGENT) == chosen
    assert await hold_audio(pool, sandbox, AGENT) == b"OggS..."
    assert await hold_of(pool, production, AGENT) is None


@postgres
async def test_silence_and_the_default_both_forget_the_clip(pool: Pool) -> None:
    org = await _org(pool)
    corner = Corner(org.id, "sandbox")
    await keep_hold(pool, corner, AGENT, Clip(b"OggS...", 3.0, "a.ogg"))
    await silence_hold(pool, corner, AGENT)
    assert await hold_of(pool, corner, AGENT) == Chosen("off")
    assert await hold_audio(pool, corner, AGENT) is None
    await forget_hold(pool, corner, AGENT)
    assert await hold_of(pool, corner, AGENT) is None


@postgres
async def test_a_caller_written_comes_back_whole_with_its_state_and_how_it_is_played(
    pool: Pool,
) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    kept = await persona(pool, org.id, "marta")
    assert kept is not None
    assert (kept.persona, kept.author) == (MARTA, "m_ana")


@postgres
async def test_a_caller_written_before_it_could_say_so_is_played_by_the_runtime(
    pool: Pool,
) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, Persona(name="juan", goal="g", style="s"), author="m_ana")
    kept = await persona(pool, org.id, "juan")
    assert kept is not None
    assert (kept.persona.llm, kept.persona.tts, kept.persona.voice) == (None, None, None)


@postgres
async def test_writing_the_same_name_replaces_it_and_never_doubles_it(pool: Pool) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    await put_persona(pool, org.id, Persona(name="marta", goal="cancel", style="s"), author="m_bo")
    (only,) = await personas_of(pool, org.id)
    assert (only.persona.goal, only.author) == ("cancel", "m_bo")


@postgres
async def test_a_rename_leaves_exactly_one_row_and_it_is_the_new_name(pool: Pool) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    renamed = Persona(name="marta-2", goal=MARTA.goal, style=MARTA.style)
    await put_persona(pool, org.id, renamed, author="m_ana", was="marta")
    assert [kept.persona.name for kept in await personas_of(pool, org.id)] == ["marta-2"]


@postgres
async def test_a_rename_is_one_statement_so_no_cut_leaves_both_names(pool: Pool) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    renames = [
        put_persona(
            pool, org.id, Persona(name=f"m{n}", goal="g", style="s"), author="a", was="marta"
        )
        for n in range(3)
    ]
    await asyncio.gather(*renames, return_exceptions=True)
    names = [kept.persona.name for kept in await personas_of(pool, org.id)]
    assert "marta" not in names


@postgres
async def test_a_rename_of_a_name_nobody_wrote_writes_nothing(pool: Pool) -> None:
    org = await _org(pool)
    with pytest.raises(NotFound, match="no persona called ghost"):
        await put_persona(pool, org.id, MARTA, author="m_ana", was="ghost")
    assert await personas_of(pool, org.id) == []


@postgres
async def test_a_rename_onto_a_name_somebody_holds_is_refused_and_moves_nothing(
    pool: Pool,
) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    await put_persona(pool, org.id, Persona(name="juan", goal="g", style="s"), author="m_ana")
    with pytest.raises(Conflict, match="a persona called juan already"):
        await put_persona(
            pool, org.id, Persona(name="juan", goal="g", style="s"), author="a", was="marta"
        )
    assert [kept.persona.name for kept in await personas_of(pool, org.id)] == ["juan", "marta"]


@postgres
async def test_dropping_one_nobody_wrote_says_so(pool: Pool) -> None:
    org = await _org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    await drop_persona(pool, org.id, "marta")
    with pytest.raises(NotFound, match="no persona called marta"):
        await drop_persona(pool, org.id, "marta")


@postgres
async def test_one_orgs_callers_are_not_anothers(pool: Pool) -> None:
    org, other = await _org(pool), await _org(pool, "bidfire")
    await put_persona(pool, org.id, MARTA, author="m_ana")
    assert await personas_of(pool, other.id) == []


@postgres
async def test_a_code_is_four_digits_written_on_the_agents_log(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    assert len(issued.code) == 4
    assert issued.code.isdigit()
    (entry,) = await _agent_log(store)
    assert (entry.type, entry.data["code"]) == (ISSUED, issued.code)


async def _agent_log(store: Store) -> list[Entry]:
    return [metered.entry for metered in await store.across([ISSUED, CLAIMED])]


@postgres
async def test_no_two_live_codes_of_one_agent_are_alike_and_fifty_is_the_most(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = [await codes.issue("sandbox", AGENT, 60, "public") for _ in range(LIVE_PER_AGENT)]
    assert len({one.code for one in issued}) == LIVE_PER_AGENT
    with pytest.raises(QuotaExhausted, match="50 codes waiting"):
        await codes.issue("sandbox", AGENT, 60, "public")


@postgres
async def test_a_code_stands_waiting_then_claimed_by_one_call_and_never_by_a_second(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    waiting = await codes.standing("sandbox", AGENT, issued.code)
    assert waiting is not None
    assert waiting.claimed is None
    claimed = await codes.claim("sandbox", AGENT, issued.code, "call_1")
    assert claimed is not None
    assert claimed.claimed == "call_1"
    assert await codes.claim("sandbox", AGENT, issued.code, "call_2") is None


@postgres
async def test_a_code_nobody_issued_or_of_the_other_world_is_nothing(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    nobodys = "0000" if issued.code != "0000" else "0001"
    assert await codes.standing("sandbox", AGENT, nobodys) is None
    assert await codes.standing("production", AGENT, issued.code) is None
    assert await codes.claim("production", AGENT, issued.code, "call_1") is None


@postgres
async def test_an_expired_code_stands_once_as_it_was_then_is_closed_with_no_call(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 0, "public")
    assert await codes.standing("sandbox", AGENT, issued.code) == issued
    assert await codes.standing("sandbox", AGENT, issued.code) is None
    closed = [entry.data["call"] for entry in await _agent_log(store) if entry.type == CLAIMED]
    assert closed == [None]


@postgres
async def test_the_page_waiting_is_answered_the_moment_a_call_claims_it(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    waiting = asyncio.create_task(codes.waited(issued, 5))
    await asyncio.sleep(0)
    await codes.claim("sandbox", AGENT, issued.code, "call_1")
    answered = await asyncio.wait_for(waiting, 1)
    assert answered.claimed == "call_1"


@postgres
async def test_a_page_that_waited_its_while_is_answered_as_the_code_stands(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    assert (await codes.waited(issued, 0.01)).claimed is None


@postgres
async def test_a_gateway_that_starts_reads_every_open_code_back_off_the_log(store: Store) -> None:
    before = Codes(Logs(store))
    open_one = await before.issue("sandbox", AGENT, 60, "public")
    taken = await before.issue("sandbox", AGENT, 60, "tenant")
    await before.claim("sandbox", AGENT, taken.code, "call_1")
    after = Codes(Logs(store))
    await after.loaded()
    reopened = await after.standing("sandbox", AGENT, open_one.code)
    still_taken = await after.standing("sandbox", AGENT, taken.code)
    assert reopened is not None
    assert still_taken is not None
    assert (reopened.claimed, still_taken.claimed, still_taken.log) == (None, "call_1", "tenant")
