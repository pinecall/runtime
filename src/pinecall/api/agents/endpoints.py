"""What a worker asks the registry about one agent: the config the app declared, resolved."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import AppKeyDep, CallsKeyDep, OverridesDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.types import AgentConfig
from pinecall_protocol.rest import AgentList, HeldAgent

router = APIRouter()

# The hop carries the domain object itself, adapted by pydantic — the same adapter
# worker/client.py validates it back through. See docs/decisions/worker.md.
CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)


@router.get("/v1/agents/{slug}/config")
async def config(
    slug: str, key: AppKeyDep, registry: RegistryDep, overrides: OverridesDep
) -> dict[str, Any]:
    """What the app declared about this agent, resolved: the session is built from it."""
    held = registry.of(key.env, slug)
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
            for held in registry.holding(key.org, key.env)
        ]
    )
