"""Per agent: its widget's look and hold melody per world, the org's personas, its caller codes."""

import asyncio

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy.agents import (
    Chosen,
    Clip,
    Look,
    forget_hold,
    hold_audio,
    hold_of,
    keep_hold,
    look_of,
    put_look,
    silence_hold,
)
from pinecall.tenancy.codes import CLAIMED, ISSUED, Codes
from pinecall.tenancy.personas import Persona, personas_of, put_persona
from pinecall.wire.frames import Entry
from tests.conftest import postgres
from tests.tenancy.conftest import MARTA, an_org

AGENT = "recepcion"


@postgres
async def test_a_widget_round_trips_per_world_and_one_nobody_set_is_the_default(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    sandbox, production = Scope(org.id, "sandbox"), Scope(org.id, "production")
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
    org = await an_org(pool)
    sandbox, production = Scope(org.id, "sandbox"), Scope(org.id, "production")
    assert await hold_of(pool, sandbox, AGENT) is None
    chosen = await keep_hold(pool, sandbox, AGENT, Clip(b"OggS...", 12.5, "jingle.ogg"))
    assert chosen.played == "custom"
    assert await hold_of(pool, sandbox, AGENT) == chosen
    assert await hold_audio(pool, sandbox, AGENT) == b"OggS..."
    assert await hold_of(pool, production, AGENT) is None


@postgres
async def test_silence_and_the_default_both_forget_the_clip(pool: Pool) -> None:
    org = await an_org(pool)
    scope = Scope(org.id, "sandbox")
    await keep_hold(pool, scope, AGENT, Clip(b"OggS...", 3.0, "a.ogg"))
    await silence_hold(pool, scope, AGENT)
    assert await hold_of(pool, scope, AGENT) == Chosen("off")
    assert await hold_audio(pool, scope, AGENT) is None
    await forget_hold(pool, scope, AGENT)
    assert await hold_of(pool, scope, AGENT) is None


@postgres
async def test_writing_the_same_name_replaces_it_and_never_doubles_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, MARTA, author="m_ana")
    await put_persona(pool, org.id, Persona(name="marta", goal="cancel", style="s"), author="m_bo")
    (only,) = await personas_of(pool, org.id)
    assert (only.persona.goal, only.author) == ("cancel", "m_bo")


async def agent_log(store: Store) -> list[Entry]:
    return [metered.entry for metered in await store.across([ISSUED, CLAIMED])]


@postgres
async def test_the_page_waiting_is_answered_the_moment_a_call_claims_it(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    waiting = asyncio.create_task(codes.waited(issued, 5))
    await asyncio.sleep(0)
    await codes.claim("sandbox", AGENT, issued.code, "call_1")
    answered = await asyncio.wait_for(waiting, 1)
    assert answered.claimed == "call_1"
