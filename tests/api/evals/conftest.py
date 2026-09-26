"""The three consent logs these tests are written against: eval-only fixtures, read once each."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from pinecall.api import deps as deps
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.api.deps import the_runs
from pinecall.api.evals.runner import Runner, the_runner
from pinecall.evals.checks.replayed import Replayed, rebuild
from pinecall.evals.runs import MemoryRuns
from pinecall.providers.models import Chat
from pinecall.types import PRODUCTION, Brought, Model, ProviderKeys
from pinecall_protocol import decode_entries, defs
from pinecall_protocol.envelope import Entry
from tests.api.conftest import A_KEY, A_RECORD
from tests.session.fake_llm import FakeLLM

# Not goldens: no reducer on either side is judged by them. They are the hand-written logs the
# judges and the ring-3 checks are pinned against, read from where tests/evals keeps them.
LOGS = Path(__file__).parents[2] / "evals" / "logs"

CONFIRMED = "booking-confirmed.json"
BEFORE_THE_YES = "booking-before-the-yes.json"
NO_GATE = "booking-with-no-gate.json"

# What the clinic declared irreversible: the one tool that takes a slot away from somebody else.
IRREVERSIBLE = frozenset({"book_appointment"})

AGENT = "clinica-norte"
AN_OWNER = "app_the_evals_doors"  # the id an app socket would have been minted
# The second agent of the fleet, held by a second app: two people testing on one gateway.
ANOTHER_AGENT = "tienda-sur"
ANOTHER_OWNER = "app_the_second_agents"
RUN = "/v1/evals/run"

# The one tool of this clinic that changes the world, declared as an app declares it. The gate's
# trace only knows which calls are irreversible because the app said so, so every consent test in
# this package is a test about this one declaration reaching the case.
BOOK = defs.ToolSpec(
    name="book_appointment",
    description="Reserva la cita y se la lee al paciente.",
    parameters={"type": "object", "properties": {"slot": {"type": "string"}}},
    side_effect="irreversible",
    confirm="Le reservo el {slot}. ¿Lo confirmo?",
)


def entries_of(name: str) -> list[Entry]:
    """One of the three logs, as the store would have handed it back."""
    return decode_entries((LOGS / name).read_text(encoding="utf-8"))


async def serving(
    registry: Registry,
    events: list[defs.EventSpec] | None = None,
    tools: list[defs.ToolSpec] | None = None,
    slug: str = AGENT,
    owner: str = AN_OWNER,
) -> None:
    """One app holding one agent on its own web door, as a register would have left it."""
    await registry.register(owner, A_RECORD.org, PRODUCTION, slug)
    await registry.configure(
        owner,
        PRODUCTION,
        slug,
        defs.AgentConfig(events=events or [], tools=tools or []),
    )


def a_golden(name: str, said: list[str], **wanted: Any) -> dict[str, Any]:
    """One golden as `pinecall test` would send it: a name, the caller's turns, and the rest."""
    return {"name": name, "input": said, **wanted}


@pytest.fixture
def confirmed() -> Replayed:
    """The call the gate was made for: asked, granted, then booked."""
    return rebuild(entries_of(CONFIRMED))


@pytest.fixture
def before_the_yes() -> Replayed:
    """A broken gate: the booking ran while the caller was still being asked."""
    return rebuild(entries_of(BEFORE_THE_YES))


@pytest.fixture
def no_gate() -> Replayed:
    """What this runtime writes today: an irreversible tool ran and nothing asked anybody."""
    return rebuild(entries_of(NO_GATE))


# The evals door takes a org's API key, and `ops_http` is already the whole gateway wired to one
# test over httpx's ASGI transport. Swapping the header is the difference between the two doors, so
# this fixture is that swap and not a second copy of nine dependency overrides.
@pytest.fixture
def keyed_http(ops_http: httpx.AsyncClient) -> httpx.AsyncClient:
    """The same ASGI client, knocking with the org's API key instead of the box's ops key."""
    ops_http.headers["Authorization"] = f"Bearer {A_KEY}"
    return ops_http


# A run needs three things `ops_http` knows nothing about, because the lifespan opens them and
# that client runs the app without one: a model a test can script, the runner that holds the
# gateway for one run, and the rows the run is written into.
@pytest.fixture
def runner() -> Runner:
    """This gateway's runner, idle at the start of every test: nothing is inherited."""
    return Runner()


@pytest.fixture
def eval_runs() -> MemoryRuns:
    """Where the runs of this test are kept, empty at the start of it."""
    return MemoryRuns()


@pytest.fixture
def suite_http(
    keyed_http: httpx.AsyncClient,
    llm: FakeLLM,
    runner: Runner,
    eval_runs: MemoryRuns,
    keys_asked: list[ProviderKeys],
    models_asked: list[Model | None],
) -> httpx.AsyncClient:
    """The keyed client with a scripted model behind it, and the run's own two resources."""

    def llms(declared: Model | None, brought: Brought) -> Chat:
        """Whatever was declared is remembered, and this test's one scripted model answers."""
        models_asked.append(declared)
        keys_asked.append(brought.keys)
        return llm

    app.dependency_overrides[deps.the_llms] = lambda: llms
    app.dependency_overrides[the_runner] = lambda: runner
    app.dependency_overrides[the_runs] = lambda: eval_runs
    return keyed_http
