"""The steps a text door takes before the first word — and the one it used to forget."""

from datetime import date

import pytest

from pinecall.api.agents.registry import Registration
from pinecall.api.calls.opening import a_text_call
from pinecall.evals import a_score
from pinecall.log.writers import Logs
from pinecall.orgs.admission import Admission
from pinecall.providers.models import Models
from pinecall.providers.overrides import Overrides
from pinecall.session.scoring import unjudged
from pinecall.types import AgentConfig, CallContext, Route
from tests.api.conftest import AGENT

pytestmark = pytest.mark.unit

A_SOCKET = "sk_opening"


def _held() -> Registration:
    """One agent as a socket holds it: the least a call needs to be opened against."""
    return Registration(
        slug=AGENT,
        org="default",
        owner=A_SOCKET,
        routes=(Route(org="default", agent=AGENT, channel="web", number=None),),
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


# A session judges nothing itself (session/scoring.py): whoever OPENS the call hands it a judge,
# and this is that place for a written call as worker/main.py is for a spoken one. Between the
# seam landing and 2026-09-09 this door handed none, so every chat and every WhatsApp call sealed
# with `not_judged` and nobody was told. The assertion is on identity, because the default is a
# perfectly working function that answers no verdict at all.
async def test_a_text_call_is_opened_with_the_judge_and_not_with_the_default(
    overrides: Overrides, llms: Models, admission: Admission, logs: Logs
) -> None:
    opened = await a_text_call(
        _held(), _context(), overrides, None, llms, admission, logs, running=0
    )

    assert opened.session._score is a_score  # pyright: ignore[reportPrivateUsage]
    assert opened.session._score is not unjudged  # pyright: ignore[reportPrivateUsage]
