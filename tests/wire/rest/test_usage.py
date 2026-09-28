"""Tests for the bodies of the org's meters."""

from pinecall.wire.rest.usage import Limit, Limits, UsagePage


def test_a_page_with_nothing_metered_says_so_in_every_field() -> None:
    assert UsagePage(rows=[], totals=None, next=None).written() == {
        "rows": [],
        "totals": None,
        "next": None,
    }


def test_a_limit_nobody_set_is_null_and_still_says_what_was_used() -> None:
    limit = Limit(limit=None, used=1.5)
    limits = Limits(
        minutes=limit,
        messages=limit,
        llm_tokens=limit,
        concurrent_calls=limit,
        agents=limit,
        seats=limit,
        numbers=limit,
        lends=None,
        billing_url=None,
        world="sandbox",
    )
    assert limits.written()["minutes"] == {"limit": None, "used": 1.5}
