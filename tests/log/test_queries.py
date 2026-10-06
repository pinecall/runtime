"""Tests for one call's facts as a gateway asks them."""

from pinecall.log.queries import facts_of_calls
from pinecall.log.store import Store
from tests.conftest import postgres
from tests.log.conftest import AGENT, ACall, judgment, logged_call


@postgres
async def test_append_folds_what_a_list_draws(store: Store, org: str) -> None:
    broken = [
        judgment("consent", "held"),
        judgment("promises", "broken", "a callback nobody booked"),
    ]
    call = await logged_call(store, org, ACall(judges=tuple(broken), took_over=True))
    facts = (await facts_of_calls(store.pool, [call]))[call]
    assert (facts.channel, facts.contact, facts.outcome, facts.cost_usd, facts.agent) == (
        "phone",
        "+34 600 111 222",
        "booked a visit",
        0.25,
        AGENT,
    )
    assert facts.score == {
        "held": 1,
        "judged": 2,
        "passed": False,
        "reason": "a callback nobody booked",
    }
    assert facts.flags == ["escalated", "low_score", "promise"]
    assert (facts.e2e, facts.spoken, facts.last_text, facts.last_in) == (
        (1.5,),
        True,
        "buenas",
        False,
    )
    assert facts.heard_at != ()
