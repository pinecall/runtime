"""The persona doors: an agent's synthetic callers and what each ran, and the org's at once."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.gateway import _deps
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.log import lists
from pinecall.log.lists import PersonaRun, PersonaRunFilters
from pinecall.providers import catalog
from pinecall.providers.declared import model_of
from pinecall.tenancy import personas
from pinecall.tenancy.personas import Persona, PersonaEdit, StoredPersona
from pinecall.wire.rest.evals import (
    PersonaList,
    PersonaRequest,
    PersonaRow,
    PersonaRunList,
    PersonaRunRow,
)

router = APIRouter()


# What `--persona` takes: lower-case words joined by hyphens.
A_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


NOT_A_NAME = (
    "{name!r} is not a persona's name: lower-case words joined by hyphens, like price-shopper"
)


A_SCREENFUL = 20


class PersonaRunQuery(BaseModel):
    """Where a persona's runs continue, and how many."""

    limit: int = Field(A_SCREENFUL, ge=1, le=_deps.LONGEST_LIST)
    before: str | None = None


class SimulationQuery(PersonaRunQuery):
    """Every simulated call, or one agent's: where the page continues, and how many."""

    agent: str | None = None


# Every agent's callers, the org's roster at once: one list for both worlds, as an agent's is.
@router.get("/v1/personas")
async def list_every_persona(key: EvalsKey, gateway: GatewayDep) -> PersonaList:
    """Every agent's callers, by agent and then by name."""
    kept = await personas.every_persona(gateway.connections.pool, key.org)
    return PersonaList(personas=[_row_of(stored) for stored in kept])


# Every simulated call of the key's world and scope, whichever persona: one agent's, or — none
# named — every agent's. The harness's one list of runs, whoever is in view.
@router.get("/v1/simulations", dependencies=[Depends(_deps.opening("evals"))])
async def list_simulations(
    scope: ScopeDep, gateway: GatewayDep, query: Annotated[SimulationQuery, Query()]
) -> PersonaRunList:
    """Every simulated call in the key's world and scope, or one agent's, newest first."""
    wanted = PersonaRunFilters(agent=query.agent, before=query.before)
    found = await lists.runs_of_persona(gateway.connections.pool, scope, wanted, limit=query.limit)
    return PersonaRunList(
        runs=[_run_row(run) for run in found.runs], total=found.total, next=found.next
    )


# One list for both worlds: a persona is test data, never heard by a customer.
@router.get("/v1/agents/{slug}/personas")
async def list_personas(slug: str, key: EvalsKey, gateway: GatewayDep) -> PersonaList:
    """The agent's callers, by name."""
    return await _list_of(gateway, key.org, slug)


# A model or a vendor this box lacks is refused when written, not on the call that plays it.
@router.put("/v1/agents/{slug}/personas/{name}")
async def put_persona(
    slug: str, name: str, body: PersonaRequest, key: EvalsKey, gateway: GatewayDep
) -> PersonaList:
    """Write one of the agent's callers whole, or rename one from `was`; its list after it."""
    if not A_NAME.match(name):
        raise DeclarationRefused(NOT_A_NAME.format(name=name))
    pool = gateway.connections.pool
    defaults = (await catalog.providers(pool)).defaults
    llm, tts = body.llm or None, body.tts or None
    model_of(llm, "llm", in_use=defaults["llm"].vendor)
    model_of(tts, "tts", in_use=defaults["tts"].vendor)
    written = Persona(
        name=name,
        goal=body.goal,
        style=body.style,
        about=body.about,
        facts=dict(body.facts),
        state=dict(body.state),
        llm=llm,
        tts=tts,
        voice=body.voice or None,
        accepts_when=body.accepts_when or "",
        declines_when=body.declines_when or "",
    )
    bearer = key.bearer.key
    edit = PersonaEdit(author=bearer.subject or bearer.key_id, was=body.was)
    await personas.put_persona(pool, key.org, slug, written, edit)
    return await _list_of(gateway, key.org, slug)


@router.delete("/v1/agents/{slug}/personas/{name}")
async def drop_persona(slug: str, name: str, key: EvalsKey, gateway: GatewayDep) -> PersonaList:
    """Forget one of the agent's callers; its list after it, 404 for a name nobody wrote."""
    await personas.drop_persona(gateway.connections.pool, key.org, slug, name)
    return await _list_of(gateway, key.org, slug)


# A name nobody wrote for the agent is a 404, not an empty page. The key is let through by the
# route's dependency, since the scope already names its org.
@router.get(
    "/v1/agents/{slug}/personas/{name}/runs", dependencies=[Depends(_deps.opening("evals"))]
)
async def list_persona_runs(
    slug: str,
    name: str,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[PersonaRunQuery, Query()],
) -> PersonaRunList:
    """The calls the persona made to the agent in the key's world and scope, newest first."""
    pool = gateway.connections.pool
    if await personas.persona(pool, scope.org, slug, name) is None:
        raise NotFound(personas.NOBODY.format(name=name, agent=slug))
    found = await lists.runs_of_persona(
        pool,
        scope,
        PersonaRunFilters(agent=slug, persona=name, before=query.before),
        limit=query.limit,
    )
    return PersonaRunList(
        runs=[_run_row(run) for run in found.runs], total=found.total, next=found.next
    )


def _run_row(run: PersonaRun) -> PersonaRunRow:
    return PersonaRunRow.model_validate(
        {
            "call": run.facts.call,
            "agent": run.facts.agent,
            "persona": run.facts.persona,
            "started_at": run.started_at,
            "ended_at": run.facts.ended_at,
            "turns": run.turns,
            "end_reason": run.facts.end_reason,
            "outcome": run.facts.outcome,
            "cost_usd": run.facts.cost_usd,
            "score": run.facts.score,
        }
    )


def _row_of(stored: StoredPersona) -> PersonaRow:
    persona = stored.persona
    return PersonaRow(
        agent=stored.agent,
        name=persona.name,
        about=persona.about,
        goal=persona.goal,
        style=persona.style,
        facts=dict(persona.facts),
        state=dict(persona.state),
        llm=persona.llm,
        tts=persona.tts,
        voice=persona.voice,
        accepts_when=persona.accepts_when,
        declines_when=persona.declines_when,
        author=stored.author,
        set_at=stored.set_at.timestamp(),
    )


async def _list_of(gateway: Gateway, org: str, agent: str) -> PersonaList:
    kept = await personas.personas_of(gateway.connections.pool, org, agent)
    return PersonaList(personas=[_row_of(stored) for stored in kept])
