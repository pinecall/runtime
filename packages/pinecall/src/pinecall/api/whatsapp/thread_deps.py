"""What opening a WhatsApp thread needs of the gateway, gathered at the request that opens it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from starlette.datastructures import State

from pinecall.api.deps import (
    AdmissionDep,
    CallIndexDep,
    GraphDep,
    LiveDep,
    LlmsDep,
    LogsDep,
    LookupsDep,
    RegistryDep,
    RoutesDep,
    SettingsDep,
    TuningDep,
    VaultDep,
)
from pinecall.live.calls import Live
from pinecall.live.registry import Registry
from pinecall.log.store.call_index import CallIndex
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.vault import Vault
from pinecall.providers.models import Models
from pinecall.routes.records import Routes
from pinecall.settings import Settings
from pinecall.whatsapp.cloud_api import Graph


# Eleven collaborators is what opening a call takes — the chat socket asks for the same ones as
# parameters of its endpoint, all but the Graph client. They are gathered into one frozen record
# here because the webhook opens a call on somebody else's behalf and hands it on, and a method
# with eleven positional arguments is a method nobody can read.
@dataclass(frozen=True)
class Doors:
    """The gateway, as far as one inbound message touches it. Built per request, held by nobody."""

    settings: Settings
    routes: Routes
    registry: Registry
    tuning: TuningStore
    vault: Vault | None
    llms: Models
    admission: Admission
    logs: Logs
    live: Live
    graph: Graph
    lookups: Lookups
    # The contact's calls, asked when this process has no thread for them: after a restart, the
    # conversation they are in is found here and taken up rather than started again.
    index: CallIndex


# Built from the request's own dependencies and not read off app.state, so what a test overrides
# at one dependency is what the webhook's doors are made of.
def get_doors(
    settings: SettingsDep,
    routes: RoutesDep,
    registry: RegistryDep,
    tuning: TuningDep,
    vault: VaultDep,
    llms: LlmsDep,
    admission: AdmissionDep,
    logs: LogsDep,
    live: LiveDep,
    graph: GraphDep,
    lookups: LookupsDep,
    index: CallIndexDep,
) -> Doors:
    """The gateway as one webhook request touches it, out of that request's dependencies."""
    return Doors(
        settings=settings,
        routes=routes,
        registry=registry,
        tuning=tuning,
        vault=vault,
        llms=llms,
        admission=admission,
        logs=logs,
        live=live,
        graph=graph,
        lookups=lookups,
        index=index,
    )


DoorsDep = Annotated[Doors, Depends(get_doors)]


# The same doors out of the gateway's own state, for the waiting room, which answers a message
# with no request behind it: a message answered from there goes the way one from Meta would.
def doors_of(state: State) -> Doors:
    """The gateway, as far as one WhatsApp message touches it."""
    return Doors(
        settings=state.settings,
        routes=state.routes,
        registry=state.registry,
        tuning=state.tuning,
        vault=state.vault,
        llms=state.llms,
        admission=state.admission,
        logs=state.logs,
        live=state.live,
        graph=state.graph,
        lookups=state.lookups,
        index=state.store,
    )
