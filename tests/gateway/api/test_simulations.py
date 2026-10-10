"""Tests for the simulation door: a persona put on whoever holds the agent, written or spoken."""

import asyncio

import pytest
from livekit import rtc

from pinecall.providers import catalog
from pinecall.providers.catalog import Providers
from pinecall.tenancy import personas
from pinecall.tenancy.personas import Persona, PersonaEdit
from tests.conftest import AGENT, Knocking, a_worker_heard, configured, postgres
from tests.fakes.livekit import Server
from tests.gateway.api.conftest import an_app

SIMULATIONS = "/v1/simulations"


def said_next(line: str, *, hanging_up: bool = False) -> list[str | dict[str, object]]:
    return [{"name": "say_next_line", "arguments": {"line": line, "hanging_up": hanging_up}}]


# The caller is played on a model of its own, as a persona names one: its script is not the agent's.
CALLER_MODEL = "acme-caller"


def scripted(agent: list[list[str]], caller: list[list[str | dict[str, object]]]) -> Providers:
    """Every stage on acme: the agent's model answering its script, the caller's model its own."""
    written = configured([list(reply) for reply in agent]).model_dump()
    written["tuning"][f"llm/acme/{CALLER_MODEL}"] = {"options": {"replies": caller}}
    return Providers.model_validate(written)


async def written_for(knocking: Knocking, agent: str = AGENT) -> None:
    apurado = Persona(
        name="apurado",
        goal="un turno",
        style="breve",
        accepts_when="a Tuesday slot",
        llm=f"acme/{CALLER_MODEL}",
    )
    await personas.put_persona(
        knocking.gateway.connections.pool, knocking.org.id, agent, apurado, PersonaEdit("a")
    )


# A simulation takes its time as a caller does: the quiet after the agent's opening and after
# each answer, so a call of two lines is a few seconds before the caller hangs up and it seals.
async def sealed(knocking: Knocking, call: str) -> list[str]:
    for _ in range(300):
        if await knocking.gateway.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    return [entry.type for entry in await knocking.gateway.logs.store.whole(call)]


# The gateway plays the caller on the chat the widget has: the process holding the agent answers
# each improvised line, the caller hangs up, and the call seals with the persona's rule on it.
@postgres
async def test_a_written_simulation_is_answered_by_whoever_holds_the_agent_and_seals(
    knocking: Knocking,
) -> None:
    caller = [said_next("quiero un turno"), said_next("perfecto, gracias", hanging_up=True)]
    agent = [["el martes a las diez"], ["de nada"]]
    await catalog.configure(knocking.gateway.connections.pool, scripted(agent, caller))
    await written_for(knocking)
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as http:
        started = await http.post(
            SIMULATIONS, json={"agent": AGENT, "persona": "apurado", "voice": False, "turns": 4}
        )
    assert started.status_code == 200, started.text
    call = started.json()["call"]
    kinds = await sealed(knocking, call)
    assert kinds[-3:] == ["call.ended", "call.summary", "call.score"], kinds
    entries = await knocking.gateway.logs.store.whole(call)
    opened = next(entry for entry in entries if entry.type == "call.started")
    assert (opened.data["persona"], opened.data["accepts_when"]) == ("apurado", "a Tuesday slot")
    users = [entry.data["text"] for entry in entries if entry.type == "turn.user"]
    assert users == ["quiero un turno", "perfecto, gracias"]
    assert "el martes a las diez" in [e.data["text"] for e in entries if e.type == "turn.agent"]
    await app.close()


# Spoken, the agent is dispatched into a room with the persona whoever holds it; the door has
# answered before the line is held, and a room that refuses the caller is closed all the same.
@postgres
async def test_a_spoken_simulation_dispatches_the_agent_with_the_persona_and_answers_at_once(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(_room: rtc.Room, _url: str, _token: str) -> None:
        raise rtc.ConnectError("the room refused the caller")

    monkeypatch.setattr(rtc.Room, "connect", refused)
    await written_for(knocking)
    app = await an_app(knocking)
    a_worker_heard(knocking.gateway.roster)
    async with knocking.http(knocking.app["sandbox"]) as http:
        started = await http.post(SIMULATIONS, json={"agent": AGENT, "persona": "apurado"})
    assert started.status_code == 200, started.text
    call = started.json()["call"]
    assert started.json()["voice"] is True
    server = knocking.gateway.connections.servers["sandbox"]
    assert isinstance(server, Server)
    for _ in range(50):
        if server.dispatcher.made:
            break
        await asyncio.sleep(0.05)
    [dispatch] = server.dispatcher.made
    assert dispatch.room == call
    assert '"persona":"apurado"' in dispatch.metadata
    assert '"accepts_when":"a Tuesday slot"' in dispatch.metadata
    await app.close()


@postgres
async def test_a_persona_nobody_wrote_or_an_agent_nobody_holds_is_refused_before_any_call(
    knocking: Knocking,
) -> None:
    await written_for(knocking, "tienda-sur")
    async with knocking.http(knocking.app["sandbox"]) as http:
        nobody_wrote = await http.post(SIMULATIONS, json={"agent": AGENT, "persona": "apurado"})
        await written_for(knocking)
        nobody_holds = await http.post(SIMULATIONS, json={"agent": AGENT, "persona": "apurado"})
    assert nobody_wrote.status_code == 404
    assert nobody_wrote.json()["detail"] == "no persona called apurado for clinica-norte"
    assert nobody_holds.status_code == 404
    assert "nobody holds clinica-norte" in nobody_holds.json()["detail"]
    assert knocking.gateway.simulations.playing == {}
