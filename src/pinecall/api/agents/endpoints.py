"""What a worker and a console ask the registry about one agent: what it declared, and its line."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import AppKeyDep, CallsKeyDep, DeclarationKeyDep, MembersDep, OverridesDep
from pinecall.api.agents.registry import NO_AGENT, Registry, RegistryDep
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.auth.members import Members
from pinecall.types import AgentConfig, DeclarationRefused
from pinecall_protocol.rest import AgentList, HeldAgent, LineHolder, TheLine

router = APIRouter()

# The hop carries the domain object itself, adapted by pydantic — the same adapter
# worker/client.py validates it back through. See docs/decisions/worker.md.
CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)


@router.get("/v1/agents/{slug}/config")
async def config(
    slug: str, key: DeclarationKeyDep, registry: RegistryDep, overrides: OverridesDep
) -> dict[str, Any]:
    """What the app declared about this agent, resolved: the session is built from it, and the
    console draws the state by it."""
    held = registry.of(key.env, slug, held_by(key))
    if held is None or held.org != key.org:
        raise HTTPException(status_code=404, detail=NO_AGENT.format(slug=slug))
    # The turned knobs are laid on through config_for(), the one applying function every door
    # that builds a session calls, so an override arrives by the path a declaration already travels.
    dumped = CONFIG.dump_python(overrides.config_for(slug, held.config), mode="json")
    return cast("dict[str, Any]", dumped)


# The console's first question, before it knows an agent to open: which agents are there. It is
# the live table and not the store, because an agent that no socket holds answers no call — the
# durable history of one is its own log, which the console already reads by slug. The envelope is
# the protocol's (protocol/schema/rest.json), so the console parses it with a generated schema.
@router.get("/v1/agents")
async def agents(key: CallsKeyDep, registry: RegistryDep) -> AgentList:
    """The org's agents in the key's world, by slug, in the order their sockets claimed them."""
    return AgentList(
        agents=[
            HeldAgent(slug=held.slug, channels=sorted(held.config.channels))
            for held in registry.holding(key.org, key.env, held_by(key))
        ]
    )


# ── the line ────────────────────────────────────────────────────────────────────

# An org shares ONE development number, so a call at it rings in one terminal. Which one is
# claimed and said out loud — before the line, the second `pinecall run` silently took the first
# one's calls and a developer dialling to test was answered in a colleague's scrollback. Alone,
# nobody claims anything: the first corner to hold an agent answers its ring. See
# api/agents/doors.py for the table, and docs/decisions/dispatch.md for why a number is shared.


@router.get("/v1/agents/{slug}/line")
async def the_line(
    slug: str, key: CallsKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Whose terminal a ring at this agent's doors lands in, and who else could take it."""
    return await _said(slug, key, registry, members)


@router.post("/v1/agents/{slug}/line")
async def claim_the_line(
    slug: str, key: AppKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Take this agent's ringing doors for this key's corner, whoever had them."""
    try:
        registry.take_the_line(key.env, slug, held_by(key))
    except DeclarationRefused as refused:
        raise HTTPException(409, str(refused)) from refused
    return await _said(slug, key, registry, members)


@router.delete("/v1/agents/{slug}/line")
async def drop_the_line(
    slug: str, key: AppKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Stop answering the ring. Whoever else is still holding the agent picks it up."""
    registry.drop_the_line(key.env, slug, held_by(key))
    return await _said(slug, key, registry, members)


async def _said(slug: str, key: KeyRecord, registry: Registry, members: Members) -> TheLine:
    """The line as a person reads it: whose it is by name, and every corner that could claim it."""
    whose = held_by(key)
    held = registry.has_a_line(key.env, slug)
    holder = registry.line_for(key.env, slug)
    waiting = [one for one in registry.waiting_for_the_line(key.env, slug) if one.holder != holder]
    return TheLine(
        agent=slug,
        env=key.env,
        held=held,
        holding=await _named(key.org, holder, members) if held else None,
        yours=held and holder == whose,
        waiting=[await _named(key.org, one.holder, members) for one in waiting],
    )


async def _named(org: str, holder: str | None, members: Members) -> LineHolder:
    """A corner, for a person. The org's own names nobody: a machine key — CI's — has no member."""
    if holder is None:
        return LineHolder(holder=None, name=None)
    member = await members.find(org, holder)
    return LineHolder(holder=holder, name=None if member is None else member.email)
