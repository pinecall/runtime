"""The persona doors: the org's synthetic callers, the agents each may call, what each ran."""

import re
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.domain.scope import Scope
from pinecall.gateway import _deps
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.log import queries
from pinecall.providers import catalog
from pinecall.providers.declared import model_of
from pinecall.tenancy import personas
from pinecall.tenancy.personas import Persona, StoredPersona
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


class PersonaQuery(BaseModel):
    """Whose personas a list asks for: one agent's, or every one of the org."""

    agent: str | None = None


class PersonaRunQuery(BaseModel):
    """Where a persona's runs continue, and how many."""

    limit: int = Field(A_SCREENFUL, ge=1, le=_deps.LONGEST_LIST)
    before: str | None = None


# One list for both worlds: a persona is test data, never heard by a customer.
@router.get("/v1/personas")
async def list_personas(
    key: EvalsKey, gateway: GatewayDep, query: Annotated[PersonaQuery, Query()]
) -> PersonaList:
    """The org's callers, by name: every one, or those an agent may be called by."""
    kept = await personas.personas_of(gateway.connections.pool, key.org, agent=query.agent)
    return PersonaList(personas=[_row_of(stored) for stored in kept])


# A model or a vendor this box lacks is refused when written, not on the call that plays it.
@router.put("/v1/personas/{name}")
async def put_persona(
    name: str, body: PersonaRequest, key: EvalsKey, gateway: GatewayDep
) -> PersonaList:
    """Write a caller whole, or rename one from `was`; the org's list after it."""
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
        agents=frozenset(body.agents),
    )
    bearer = key.bearer.key
    await personas.put_persona(
        pool, key.org, written, author=bearer.subject or bearer.key_id, was=body.was
    )
    return PersonaList(
        personas=[_row_of(stored) for stored in await personas.personas_of(pool, key.org)]
    )


@router.delete("/v1/personas/{name}")
async def drop_persona(name: str, key: EvalsKey, gateway: GatewayDep) -> PersonaList:
    """Forget a caller; the org's list after it, 404 for a name nobody wrote."""
    pool = gateway.connections.pool
    await personas.drop_persona(pool, key.org, name)
    return PersonaList(
        personas=[_row_of(stored) for stored in await personas.personas_of(pool, key.org)]
    )


# A name the org never wrote is a 404, not an empty page.
@router.get("/v1/personas/{name}/runs")
async def list_persona_runs(
    name: str,
    key: EvalsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[PersonaRunQuery, Query()],
) -> PersonaRunList:
    """The calls the persona made in the key's world and scope, newest first, a page."""
    pool = gateway.connections.pool
    if await personas.persona(pool, key.org, name) is None:
        raise NotFound(personas.NOBODY.format(name=name))
    found = await queries.runs_of_persona(
        pool, Scope(key.org, scope.env, scope.holder), name, before=query.before, limit=query.limit
    )
    rows = [
        PersonaRunRow.model_validate(
            {
                "call": run.facts.call,
                "agent": run.facts.agent,
                "started_at": run.started_at,
                "ended_at": run.facts.ended_at,
                "turns": run.turns,
                "end_reason": run.facts.end_reason,
                "outcome": run.facts.outcome,
                "cost_eur": run.facts.cost_eur,
                "score": run.facts.score,
            }
        )
        for run in found.runs
    ]
    return PersonaRunList(runs=rows, total=found.total, next=found.next)


def _row_of(stored: StoredPersona) -> PersonaRow:
    persona = stored.persona
    return PersonaRow(
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
        agents=sorted(persona.agents),
    )
