"""What opening a WhatsApp thread needs of the gateway, gathered at the request that opens it."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall._settings import Settings
from pinecall.api._live import Live
from pinecall.api.agents.registry import Registry
from pinecall.knowledge import Knowledge
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning import TuningStore
from pinecall.orgs.vault import Vault
from pinecall.providers.models import Models
from pinecall.routes.table import Routes
from pinecall.whatsapp.graph import Graph


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
    knowledge: Knowledge | None = None
