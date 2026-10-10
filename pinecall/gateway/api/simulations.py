"""The simulation door: a persona put on the agent, written or spoken, against whoever holds it."""

from fastapi import APIRouter

from pinecall.domain.call import new_call_id
from pinecall.domain.errors import NotFound
from pinecall.domain.scope import Scope
from pinecall.evals import spoken
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.simulating.caller import (
    Placed,
    a_new_call,
    spoken_call,
    written_call,
    written_turns,
)
from pinecall.session.session import Session
from pinecall.tenancy import personas
from pinecall.tenancy.keys import check_agent
from pinecall.tenancy.personas import Persona
from pinecall.wire.rest.evals import CallerPersona, SimulationRequest, SimulationStarted

router = APIRouter()


NOBODY_HOLDS = (
    "nobody holds {agent} in this world: a simulation calls the agent as a caller would, so a "
    "process must be answering it — deployed, or `pinecall start` in its directory"
)


# The gateway plays the caller, so the agent is called as anybody calls it: the process that
# holds it — deployed with `pinecall deploy`, on a server of the org's, or a developer's
# `pinecall start` — answers, and nothing about the agent changes for a simulation. What can be
# refused is refused before the answer: the persona, nobody holding the agent, the org's limits,
# a call id taken. The conversation is played after it, and watched as any call is.
@router.post("/v1/simulations")
async def simulate(
    body: SimulationRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> SimulationStarted:
    """Put one of the agent's personas on it, written or spoken; the call's id at once."""
    check_agent(key.bearer, body.agent)
    kept = await personas.persona(gateway.connections.pool, key.org, body.agent, body.persona)
    if kept is None:
        raise NotFound(personas.NOBODY.format(name=body.persona, agent=body.agent))
    registration = gateway.sockets.serving(scope, body.agent, None)
    if registration is None:
        raise NotFound(NOBODY_HOLDS.format(agent=body.agent))
    line = spoken.Line(interferer_db=body.interferer_db, packet_loss=body.packet_loss)
    persona = kept.persona
    placed = Placed(
        new_call_id(), body.agent, calling_as(persona), body.turns, line, dict(persona.state)
    )
    if body.voice:
        await a_new_call(gateway, placed.call, body.agent, scope)
        gateway.simulations.play(placed.call, _spoken(gateway, scope, placed))
    else:
        session = await written_call(gateway, registration, placed)
        gateway.simulations.play(placed.call, _written(gateway, session, placed))
    return SimulationStarted(call=placed.call, voice=body.voice)


def calling_as(persona: Persona) -> CallerPersona:
    """The persona as the caller is played: who, how, what it knows, and its rules for the judge."""
    return CallerPersona(
        name=persona.name,
        goal=persona.goal,
        style=persona.style,
        facts=dict(persona.facts),
        llm=persona.llm,
        tts=persona.tts,
        voice=persona.voice,
        accepts_when=persona.accepts_when,
        declines_when=persona.declines_when,
    )


async def _spoken(gateway: Gateway, scope: Scope, placed: Placed) -> None:
    await spoken_call(gateway, scope, placed)


async def _written(gateway: Gateway, session: Session, placed: Placed) -> None:
    await written_turns(gateway, session, placed)
