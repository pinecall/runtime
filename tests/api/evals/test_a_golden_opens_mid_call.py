"""A golden is one turn under the state it declares, so the agent's opening never sounds in one."""

from functools import partial

import pytest

from pinecall._settings import Budgets
from pinecall.api._live import Live
from pinecall.api.evals.conversation import an_eval_call
from pinecall.evals.goldens import Golden
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.vault import keys_brought_by
from pinecall.session.text.session import TextSession
from pinecall.types import PRODUCTION, AgentConfig, Greeting
from tests.lookups.fakes import a_plan, the_tenants
from tests.session.fake_llm import FakeLLM

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_golden_opens"
A_RUN = "run_the_one_that_opened_it"
AGENT = "clinica-norte"
ORG = "org_the_clinic"
THE_WORDS = "Clínica Norte, buenos días."


def a_call_of(greeting: Greeting) -> tuple[TextSession, MemoryStore]:
    """The call a golden opens for an agent that greets every real caller it answers."""
    golden = Golden.model_validate({"name": "ofrece-las-horas-del-martes", "input": ["hola"]})
    config = AgentConfig(slug=AGENT, greeting=greeting)
    store = MemoryStore()
    logs = Logs(store)
    lookups = Lookups(
        None, None, logs, Live(), partial(keys_brought_by, None), *a_plan(logs, the_tenants())
    )
    return an_eval_call(
        golden, A_CALL, A_RUN, config, ORG, PRODUCTION, logs, FakeLLM(), lookups, Budgets()
    ), store


async def test_the_class_keeps_its_greeting_and_the_call_knows_which_run_opened_it() -> None:
    """One rule, at the opening: the session reads `context.run`; the config is not rewritten."""
    session, _ = a_call_of(Greeting(say=THE_WORDS))
    assert session.config.greeting == Greeting(say=THE_WORDS)
    assert session.context.run == A_RUN


async def test_a_golden_run_writes_no_opening_turn_at_all() -> None:
    """The field being None would be decoration if start() still put a turn on the log."""
    session, store = a_call_of(Greeting(say=THE_WORDS))

    await session.start()

    assert [entry.type for entry in await store.since(A_CALL)] == ["call.started"]


async def test_an_improvised_opening_is_dropped_too_and_costs_no_model_call() -> None:
    """A golden that paid for a greeting turn would price every suite by a sentence nobody read."""
    session, store = a_call_of(Greeting(reply="saluda y preséntate"))

    await session.start()

    assert [entry.type for entry in await store.since(A_CALL)] == ["call.started"]
