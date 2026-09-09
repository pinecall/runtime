"""What a reader may ask for, and the four types no filter can take away."""

import pytest

from pinecall.log.entry import Entry
from pinecall.log.filters import ALWAYS_PASS, EVERYTHING, MAX_TYPES, Filter, FilterRefused

pytestmark = pytest.mark.unit


def an_entry(type: str, ephemeral: bool = False) -> Entry:
    """One entry of the type under discussion; nothing else about it matters here."""
    return Entry(seq=1, ts=1.0, call="CA_1", agent="a", type=type, ephemeral=ephemeral, data={})


def test_the_default_filter_narrows_nothing() -> None:
    assert EVERYTHING.passes(an_entry("turn.user"))
    assert EVERYTHING.passes(an_entry("metrics.vad", ephemeral=True))


def test_types_keeps_only_what_it_names() -> None:
    filter = Filter.of("turn.user,turn.agent")
    assert filter.passes(an_entry("turn.user")) and filter.passes(an_entry("turn.agent"))
    assert not filter.passes(an_entry("metrics.llm"))


def test_durable_drops_what_a_store_may_forget() -> None:
    filter = Filter.of(durable=True)
    assert filter.passes(an_entry("turn.user"))
    assert not filter.passes(an_entry("user.transcript", ephemeral=True))


@pytest.mark.parametrize("type", sorted(ALWAYS_PASS))
def test_the_always_pass_set_survives_every_filter(type: str) -> None:
    """Two say the stream lost something and two say the call is over: a reader must hear both."""
    narrowest = Filter.of("custom", durable=True)
    assert narrowest.passes(an_entry(type, ephemeral=True))


def test_a_trailing_comma_is_not_a_type() -> None:
    assert Filter.of("turn.user,").types == frozenset({"turn.user"})


def test_too_many_types_is_refused_by_the_number() -> None:
    with pytest.raises(FilterRefused, match=str(MAX_TYPES)):
        Filter.of(",".join(f"custom.{n}" for n in range(MAX_TYPES + 1)))


@pytest.mark.parametrize("name", ["Turn.User", "turn user", "turn/../etc", "turn;drop"])
def test_a_name_off_the_charset_is_refused(name: str) -> None:
    with pytest.raises(FilterRefused, match="lowercase words"):
        Filter.of(name)
