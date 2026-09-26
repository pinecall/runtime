"""The app holding the agent closes mid-run: the run stops there, and says how far it got."""

import asyncio
import time
from typing import Any, override

import httpx
import pytest

from pinecall.api.evals.app_settled import AT_MOST_S
from pinecall.api.evals.run_attachment import APP_DETACHED
from pinecall.live.registry import Registry
from pinecall.log.replay import whole
from pinecall.log.store import MemoryStore
from pinecall_testkit.fake_llm import FakeLLM, Scripted, ScriptedStream
from tests.api.evals.conftest import AGENT, AN_OWNER, RUN, a_golden, serving

# The judges are the `evals` group, not a dependency of the gateway: on a box without it the door
# answers 503 and this file has nothing to assert. The whole module skips, naming the command.

pytestmark = pytest.mark.unit

# Three goldens, so that one run holds the three states criterion 2 is about: one scored, one in
# flight when the socket closed, and one never opened at all.
THREE = [
    a_golden("greets", ["hola"], expect={"says": ["Clara"]}),
    a_golden("prices", ["¿cuánto cuesta?"], expect={"says": ["45"]}),
    a_golden("farewell", ["gracias, adiós"], expect={"says": ["hasta"]}),
]

# Which request the app goes away on. One golden of one turn is one request, so this is the second
# conversation being answered: the first has been scored, and the third has not been opened.
THE_SECOND_CONVERSATION = 1


class Leaving(FakeLLM):
    """The scripted model, with the app's socket closing behind it on the request it is told."""

    def __init__(self, registry: Registry, on_request: int) -> None:
        super().__init__()
        self._registry = registry
        self._on_request = on_request
        self.left_at: float | None = None

    # The closest a unit test gets to a person closing the terminal that holds the class: the
    # socket stops holding the agent mid-conversation, exactly as `WS /v1/apps` releases it.
    @override
    def chat(self, **asked: Any) -> ScriptedStream:
        """The next scripted answer, and — once — the app going away as it is asked for."""
        if len(self.asked) == self._on_request and self.left_at is None:
            self.left_at = time.time()
            # The table is cleared before the first await inside release: the watch sees the
            # socket gone on its next look, and agent.detached lands a tick later.
            asyncio.ensure_future(self._registry.release(AN_OWNER))  # noqa: RUF006
        return super().chat(**asked)


@pytest.fixture
def llm(registry: Registry) -> FakeLLM:
    """This module's model: the one that lets the app go while the second golden is answered."""
    leaving = Leaving(registry, THE_SECOND_CONVERSATION)
    # Only the first golden is ever scored, so only its answer has to hold up to a judge.
    leaving.script.append(Scripted(chunks=("Buenos días, soy Clara.",)))
    return leaving


@pytest.fixture
def leaving(llm: FakeLLM) -> Leaving:
    """The same model, as the thing a test asks when the app actually left."""
    assert isinstance(llm, Leaving)
    return llm


async def test_a_run_whose_app_leaves_fails_with_the_partial_matrix(
    suite_http: httpx.AsyncClient, registry: Registry, leaving: Leaving
) -> None:
    """Criteria 1 and 2: the error names how far it got, and only the scored golden has a cell."""
    await serving(registry)

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": THREE})
    stopped_at = time.time()

    assert answered.status_code == 200, answered.text
    run: dict[str, Any] = answered.json()
    assert run["status"] == "failed"
    assert "the app detached after 1 of 3 goldens (clinica-norte)" in run["error"]
    assert leaving.left_at is not None
    assert stopped_at - leaving.left_at < AT_MOST_S
    # The golden that was scored keeps its cell; the one in flight and the one never reached are
    # absent from the matrix rather than counted as broken.
    assert run["matrix"]["goldens"] == ["greets"]
    assert [cell["golden"] for cell in run["matrix"]["runs"]] == ["greets"]
    assert not run["matrix"]["failures"]


async def test_a_run_opens_no_golden_once_the_app_has_gone(
    suite_http: httpx.AsyncClient, registry: Registry
) -> None:
    """The third golden is never opened at all: the run's calls stop where the app did."""
    await serving(registry)

    run: dict[str, Any] = (
        await suite_http.post(RUN, json={"agent": AGENT, "goldens": THREE})
    ).json()

    assert [opened["golden"] for opened in run["calls"]] == ["greets", "prices"]


async def test_the_call_the_app_left_ends_as_app_detached_and_not_on_a_timeout(
    suite_http: httpx.AsyncClient, registry: Registry, store: MemoryStore, leaving: Leaving
) -> None:
    """Criterion 3: the conversation in flight ends immediately, in its own log, saying why."""
    await serving(registry)

    run: dict[str, Any] = (
        await suite_http.post(RUN, json={"agent": AGENT, "goldens": THREE})
    ).json()

    left = run["calls"][-1]["call"]
    ended = [entry for entry in await whole(store, left) if entry.type == "call.ended"]
    assert [entry.data["reason"] for entry in ended] == [APP_DETACHED]
    assert [entry.data["ended_by"] for entry in ended] == ["platform"]
    assert leaving.left_at is not None
    # Ended by the detach, not by anything that waited its own deadline out first.
    assert float(ended[0].data["ended_at"]) - leaving.left_at < AT_MOST_S
