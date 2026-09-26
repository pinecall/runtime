"""A golden that names a weekday pins the day it means, so it reads the same in a year."""

import json
from datetime import date
from functools import partial
from typing import Any

import pytest
from livekit.agents import llm as agents

from pinecall._settings import Budgets
from pinecall.api.evals.golden_call import open_eval_call
from pinecall.api.live import Live
from pinecall.evals.goldens import Golden
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.vault import brought_by
from pinecall.session import date_tool
from pinecall.session.text.session import TextSession
from pinecall.types import PRODUCTION, AgentConfig
from tests.lookups.fakes import a_plan, the_tenants
from tests.session.fake_llm import FakeLLM

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_golden_opens"
A_RUN = "run_the_one_that_opened_it"
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
    config = AgentConfig(slug=AGENT)
    logs = Logs(MemoryStore())
    lookups = Lookups(
        None,
        None,
        logs,
        Live(),
        partial(brought_by, None, the_tenants().quotas_of),
        *a_plan(logs, the_tenants()),
    )
    return open_eval_call(
        golden, A_CALL, A_RUN, config, ORG, PRODUCTION, logs, FakeLLM(), lookups, Budgets()
    )


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
        if isinstance(item, agents.FunctionCallOutput) and item.name == date_tool.CLOCK_TOOL
    ]
    assert dates == [{"today": A_TUESDAY, "weekday": "tuesday"}]
