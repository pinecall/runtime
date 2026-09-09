"""A golden that names a weekday pins the day it means, so it reads the same in a year."""

import json
from datetime import date
from typing import Any

import pytest
from livekit.agents import llm as agents

from pinecall.api.evals.conversation import an_eval_call
from pinecall.evals.goldens import Golden
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.session import clock
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig
from tests.session.fake_llm import FakeLLM

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_golden_opens"
AGENT = "clinica-norte"
ORG = "org_the_clinic"
A_TUESDAY = "2026-09-08"


def a_golden(**written: Any) -> Golden:
    """One golden as a tenant writes it on disk: a name, a turn, and whatever this test pins."""
    return Golden.model_validate(
        {"name": "ofrece-las-horas-del-martes", "input": ["hola"], **written}
    )


def a_call_of(golden: Golden) -> TextSession:
    """The call that golden opens, on a model nobody in this file ever reaches."""
    config = AgentConfig(slug=AGENT, channels=frozenset({"web"}))
    return an_eval_call(golden, A_CALL, config, ORG, Logs(MemoryStore()), FakeLLM())


async def test_a_golden_that_names_no_day_runs_on_the_real_one() -> None:
    """Nothing pinned is the common case: the run happens today, as every call does."""
    assert a_call_of(a_golden()).context.today == date.today()


async def test_a_golden_pins_the_day_it_means_and_the_call_is_opened_on_it() -> None:
    assert a_call_of(a_golden(today=A_TUESDAY)).context.today == date(2026, 9, 8)


async def test_the_pinned_day_is_the_one_the_model_is_told_it_is() -> None:
    """The field would be decoration if the history did not carry it: the pair says tuesday."""
    session = a_call_of(a_golden(today=A_TUESDAY))

    await session.start()

    dates = [
        json.loads(item.output)
        for item in session.text_agent.chat_ctx.items
        if isinstance(item, agents.FunctionCallOutput) and item.name == clock.CLOCK_TOOL
    ]
    assert dates == [{"today": A_TUESDAY, "weekday": "tuesday"}]
