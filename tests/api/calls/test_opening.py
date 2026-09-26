"""The steps a text door takes before the first word — and the one it used to forget."""

from datetime import date

import pytest

from pinecall._settings import Budgets
from pinecall.api.agents.held_agent import Registration
from pinecall.api.calls.opening import a_text_call
from pinecall.evals import a_score
from pinecall.evals.hangup_score import JudgedWhen
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning_store import MemoryTuning
from pinecall.providers.models import Models
from pinecall.session.score_step import unjudged_score
from pinecall.types import PRODUCTION, AgentConfig, CallContext, Route
from tests.api.conftest import AGENT

pytestmark = pytest.mark.unit

A_SOCKET = "sk_opening"


def _held() -> Registration:
    """One agent as a socket holds it: the least a call needs to be opened against."""
    return Registration(
        slug=AGENT,
        org="default",
        env=PRODUCTION,
        owner=A_SOCKET,
        config=AgentConfig(slug=AGENT),
    )


def _context() -> CallContext:
    """A written call arriving on the web door."""
    return CallContext(
        call="CA_opening",
        channel="web",
        direction="inbound",
        caller="ct_opening",
        route=Route(org="default", agent=AGENT, channel="web", number=None),
        today=date(2026, 9, 9),
    )


# A session judges nothing itself (session/score_step.py): whoever OPENS the call hands it a judge,
# and this is that place for a written call as worker/main.py is for a spoken one. Between the
# seam landing and 2026-09-09 this door handed none, so every chat and every WhatsApp call sealed
# with `not_judged` and nobody was told. The assertion is on identity, because the default is a
# perfectly working function that answers no verdict at all.
async def test_a_text_call_is_opened_with_the_judge_and_not_with_the_default(
    tuning: MemoryTuning, llms: Models, admission: Admission, logs: Logs, lookups: Lookups
) -> None:
    opened = await a_text_call(
        _held(), _context(), tuning, None, llms, admission, logs, 0, lookups, Budgets()
    )

    judge = opened.session._score  # pyright: ignore[reportPrivateUsage]
    assert isinstance(judge, JudgedWhen) and judge.score is a_score
    assert opened.session._score is not unjudged_score  # pyright: ignore[reportPrivateUsage]
